"""
model.py — ML-часть сервиса.

Для бэкенда:
    from model import analyze, get_model
    get_model()                       # один раз при старте сервера
    result = analyze("видео.mov")     # {"verdict": ..., "violations": [...]} — формат ТЗ
    # job_id и status добавляет бэкенд.

Режимы (ml_config.PIPELINE):
  checklist (основной): роли -> модель отвечает да/нет на вопросы по каждому кадру (1/с)
                        -> rules.py находит нарушения по критериям аналитиков
                        -> описание по шаблону аналитиков + норма + кадры-доказательства
  two_stage / direct:   прежние режимы (модель сама выбирает нарушение из списка)
"""
import contextlib
import hashlib
import json
import re
from pathlib import Path

import cv2
import torch
from PIL import Image

import detector
import ml_config as cfg
import rules
from frames import extract_frames, to_base64_jpeg

_model = None
_processor = None
_batch_ok = True


# ---------------------------------------------------------------- модель

def get_model(with_lora=True):
    """Загружает модель один раз и держит в памяти.
    В режиме lora поверх базовой модели подключается дообученный адаптер (outputs/lora)."""
    global _model, _processor
    if _model is None:
        from transformers import AutoProcessor
        try:
            from transformers import Qwen3VLForConditionalGeneration as ModelCls
        except ImportError:
            from transformers import AutoModelForImageTextToText as ModelCls
        _model = ModelCls.from_pretrained(cfg.MODEL_PATH, dtype=torch.bfloat16, device_map="auto")
        if with_lora and cfg.PIPELINE in ("lora", "ensemble"):
            from peft import PeftModel
            if not (Path(cfg.LORA_PATH) / "adapter_config.json").exists():
                raise FileNotFoundError(f"Нет адаптера в {cfg.LORA_PATH}: сначала запустите train_lora.py")
            _model = PeftModel.from_pretrained(_model, str(cfg.LORA_PATH))
            if cfg.PIPELINE == "lora":
                _model = _model.merge_and_unload()  # только lora — встраиваем адаптер, так быстрее
        _model.eval()
        _processor = AutoProcessor.from_pretrained(cfg.MODEL_PATH)
    return _model, _processor


def _ask(messages, max_new_tokens=None):
    model, processor = get_model()
    inputs = processor.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_dict=True, return_tensors="pt",
    ).to(model.device)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens or cfg.MAX_NEW_TOKENS, do_sample=False)
    trimmed = out[:, inputs["input_ids"].shape[1]:]
    return processor.batch_decode(trimmed, skip_special_tokens=True)[0]


def _ask_batch(conversations, max_new_tokens):
    """Несколько запросов за раз (быстрее). Если пакетный режим не работает — по одному."""
    global _batch_ok
    if _batch_ok and len(conversations) > 1:
        try:
            model, processor = get_model()
            processor.tokenizer.padding_side = "left"
            common = dict(tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt")
            try:  # transformers 5.x: параметры процессора передаются отдельным словарём
                inputs = processor.apply_chat_template(conversations, processor_kwargs={"padding": True}, **common)
            except TypeError:  # старые версии transformers
                inputs = processor.apply_chat_template(conversations, padding=True, **common)
            inputs = inputs.to(model.device)
            with torch.inference_mode():
                out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
            trimmed = out[:, inputs["input_ids"].shape[1]:]
            return processor.batch_decode(trimmed, skip_special_tokens=True)
        except Exception as e:  # noqa: BLE001
            print(f"[model] пакетный режим не сработал ({e}); дальше по одному кадру")
            _batch_ok = False
    return [_ask(c, max_new_tokens) for c in conversations]


def _base_only():
    """Контекст, в котором отвечает ИСХОДНАЯ модель (адаптер LoRA временно выключен)."""
    if _model is not None and hasattr(_model, "disable_adapter"):
        return _model.disable_adapter()
    return contextlib.nullcontext()


# ---------------------------------------------------------------- вспомогательное

def _to_pil(frame, scale=1.0):
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    long_side = max(img.width, img.height)
    if long_side > cfg.MAX_FRAME_SIDE:  # большое видео (1080p и т.п.) — уменьшаем, иначе слишком долго
        k = cfg.MAX_FRAME_SIDE / long_side
        img = img.resize((int(img.width * k), int(img.height * k)), Image.BICUBIC)
    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.BICUBIC)
    return img


def _parse_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _nearest(frames, t):
    return min(frames, key=lambda tf: abs(tf[0] - t))


def _frames_content(window, scale=1.0):
    content = []
    for t, frame in window:
        content.append({"type": "text", "text": f"[{t:.1f} с]"})
        content.append({"type": "image", "image": _to_pil(frame, scale)})
    return content


def _roles(frames):
    step = max(1, len(frames) // 8)
    sample = frames[::step][:8]
    content = _frames_content(sample) + [{"type": "text", "text": cfg.ROLES_PROMPT}]
    return _ask([{"role": "user", "content": content}], cfg.ROLES_MAX_TOKENS).strip()


def _evidence(frames, t):
    main_t, main_f = _nearest(frames, t)
    evidence = [(main_t, main_f)]
    for dt in (-1.0, 1.0):
        t2, f2 = _nearest(frames, t + dt)
        if all(abs(t2 - e[0]) > 0.01 for e in evidence):
            evidence.append((t2, f2))
    return main_t, [{"timestamp_sec": round(tt, 1), "image_base64": to_base64_jpeg(ff)} for tt, ff in evidence[:3]]


# ---------------------------------------------------------------- режим checklist

def _settings_key():
    s = json.dumps([cfg.OBS_FPS, cfg.MAX_OBS_FRAMES, cfg.OBS_SCALE, cfg.SAMPLE_FPS,
                    cfg.build_obs_prompt("{roles}"), cfg.ROLES_PROMPT], ensure_ascii=False)
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:10]


def _parse_flags(text):
    d = _parse_json(text) or {}
    out = {}
    for k in cfg.OBS_FIELDS:
        v = d.get(k, 0)
        out[k] = 1 if str(v).strip().lower() in ("1", "true", "да", "yes") else 0
    return out


def _report(progress, stage, percent, message=""):
    if progress:
        try:
            progress(stage, int(percent), message)
        except Exception:  # noqa: BLE001  — прогресс не должен ломать анализ
            pass


def observe(frames, progress=None):
    """Роли + ответы модели по кадрам: (roles, obs)."""
    _report(progress, "roles", 8, "Определение ролей людей")
    roles = _roles(frames)
    duration = frames[-1][0] if frames else 0.0
    fps = min(cfg.OBS_FPS, cfg.MAX_OBS_FRAMES / max(duration, 1.0))
    k = max(1, round(cfg.SAMPLE_FPS / fps))
    selected = frames[::k]
    prompt = cfg.build_obs_prompt(roles)
    convs = [[{"role": "user", "content": [{"type": "image", "image": _to_pil(f, cfg.OBS_SCALE)},
                                           {"type": "text", "text": prompt}]}] for _, f in selected]
    answers = []
    for i in range(0, len(convs), cfg.OBS_BATCH):
        _report(progress, "checklist", 12 + 48 * i / max(1, len(convs)),
                f"Анализ кадров: {i}/{len(convs)}")
        answers += _ask_batch(convs[i:i + cfg.OBS_BATCH], cfg.OBS_MAX_TOKENS)
    obs = [{"t": round(t, 2), "f": _parse_flags(a)} for (t, _), a in zip(selected, answers)]
    return roles, obs


def cache_path(video_path):
    return Path(cfg.OBS_CACHE_DIR) / f"{Path(video_path).stem}.json"


def load_cached_obs(video_path):
    p = cache_path(video_path)
    if not p.exists():
        return None
    data = json.load(open(p, encoding="utf-8"))
    return data if data.get("settings") == _settings_key() else None


def det_cache_path(video_path):
    return Path(cfg.OBS_CACHE_DIR).parent / "det" / f"{Path(video_path).stem}.json"


def detections_for(video_path, frames, obs, use_cache=False):
    """Ответы детектора для тех же моментов, что и вопросы к Qwen. None — детектора нет."""
    if not detector.available():
        return None
    p = det_cache_path(video_path)
    stamp = detector.stamp()
    if use_cache and p.exists():
        data = json.load(open(p, encoding="utf-8"))
        if data.get("stamp") == stamp and len(data.get("det", [])) == len(obs):
            return data["det"]
    times = [o["t"] for o in obs]
    imgs = [_to_pil(_nearest(frames, t)[1]) for t in times]
    det = [{"t": t, "found": f} for t, f in zip(times, detector.detect(imgs))]
    if use_cache:
        p.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"stamp": stamp, "det": det}, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return det


def result_from_obs(frames, obs, det=None):
    extra = ()
    if det:
        obs = detector.apply(obs, det)
        extra = cfg.DET_EXTRA_RULES
    violations = []
    for e in rules.detect(obs, extra):
        main_t, ev = _evidence(frames, e["t"])
        violations.append({
            "type": cfg.type_name(e["id"]),
            "timecode_sec": round(main_t, 1),
            "description": rules.render_description(e["id"], main_t, e["dur"], e.get("n", 0)),
            "evidence_frames": ev,
        })
    return {"verdict": "violation" if violations else "no_violation", "violations": violations}


# ---------------------------------------------------------------- режимы two_stage / direct

def _make_windows(frames):
    if not frames:
        return []
    end = frames[-1][0]
    windows, start = [], 0.0
    while True:
        w = [(t, f) for t, f in frames if start <= t < start + cfg.WINDOW_SEC]
        if w:
            windows.append(w)
        if start + cfg.WINDOW_SEC >= end:
            break
        start += cfg.STEP_SEC
    return windows


def _text_messages(text):
    return [
        {"role": "system", "content": [{"type": "text", "text": cfg.SYSTEM_PROMPT}]},
        {"role": "user", "content": [{"type": "text", "text": text}]},
    ]


def _norm_id(raw):
    s = str(raw or "").upper()
    if "OTHER" in s:
        return cfg.OTHER_ID
    m = re.search(r"V\s*-?\s*(\d+)", s)
    if not m:
        return None
    tid = f"V{int(m.group(1)):02d}"
    return tid if tid in cfg.TYPES else None


def _to_float(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _items(parsed):
    if not isinstance(parsed, dict):
        return []
    if isinstance(parsed.get("violations"), list):
        return [v for v in parsed["violations"] if isinstance(v, dict)]
    if parsed.get("violation"):
        return [{"id": parsed.get("type"), **parsed}]
    return []


def _llm_description(type_id, observed):
    parts = []
    observed = (observed or "").strip()
    if observed:
        parts.append(observed if observed.endswith(".") else observed + ".")
    conclusion = ""
    if type_id in cfg.TYPES:
        tpl = cfg.TYPES[type_id].get("template", "")
        i = tpl.find("Это может")
        conclusion = tpl[i:].strip() if i >= 0 else ""
    parts.append(conclusion or "Требуется проверка человеком.")
    ref = cfg.norm_reference(type_id)
    if ref:
        parts.append(f"Основание: {ref}.")
    return " ".join(parts)


def _llm_found(frames, raw):
    windows = _make_windows(frames)
    found = []
    if cfg.PIPELINE == "two_stage":
        roles = _roles(frames)
        raw.append({"roles": roles})
        describe_prompt = cfg.DESCRIBE_PROMPT.replace("{roles}", roles)
        parts = []
        for window in windows:
            w_start, w_end = window[0][0], window[-1][0]
            content = _frames_content(window) + [{"type": "text", "text": describe_prompt}]
            desc = _ask([{"role": "user", "content": content}], cfg.DESCRIBE_MAX_TOKENS)
            raw.append({"window": [w_start, w_end], "description": desc})
            parts.append(f"--- Фрагмент {w_start:.1f}–{w_end:.1f} с ---\n{desc.strip()}")
        answer = _ask(_text_messages(cfg.build_judge_prompt("\n\n".join(parts), roles)), cfg.JUDGE_MAX_TOKENS)
        raw.append({"judge": answer})
        found = [(item, 0.0, frames[-1][0] if frames else 0.0) for item in _items(_parse_json(answer))]
    else:
        for window in windows:
            w_start, w_end = window[0][0], window[-1][0]
            content = _frames_content(window) + [{"type": "text", "text": cfg.build_question()}]
            answer = _ask([{"role": "system", "content": [{"type": "text", "text": cfg.SYSTEM_PROMPT}]},
                           {"role": "user", "content": content}])
            raw.append({"window": [w_start, w_end], "answer": answer})
            found += [(item, w_start, w_end) for item in _items(_parse_json(answer))]
    return found


def _llm_result(frames, raw):
    candidates = []
    for item, w_start, w_end in _llm_found(frames, raw):
        tid = _norm_id(item.get("id"))
        if tid is None or tid in cfg.VERDICT_EXCLUDE:
            continue
        conf = _to_float(item.get("confidence"), 0.5)
        if conf < (cfg.OTHER_THRESHOLD if tid == cfg.OTHER_ID else cfg.CONF_THRESHOLD):
            continue
        t = _to_float(item.get("time_sec"), (w_start + w_end) / 2)
        candidates.append({"id": tid, "time": min(max(t, w_start), w_end),
                           "description": str(item.get("description", "")), "conf": conf})
    kept = []
    for c in sorted(candidates, key=lambda c: -c["conf"]):
        if all(not (k["id"] == c["id"] and abs(k["time"] - c["time"]) < cfg.MERGE_SEC) for k in kept):
            kept.append(c)
    kept = kept[:cfg.MAX_VIOLATIONS]
    kept.sort(key=lambda c: c["time"])
    violations = []
    for c in kept:
        main_t, ev = _evidence(frames, c["time"])
        violations.append({"type": cfg.type_name(c["id"]), "timecode_sec": round(main_t, 1),
                           "description": _llm_description(c["id"], c["description"]), "evidence_frames": ev})
    return {"verdict": "violation" if violations else "no_violation", "violations": violations}


# ---------------------------------------------------------------- режим lora

def lora_windows(frames, step):
    """[(начало, [(t, кадр), ...]), ...] — окна длиной LORA_WINDOW по кадрам с частотой LORA_FPS."""
    k = max(1, round(cfg.SAMPLE_FPS / cfg.LORA_FPS))
    fr = frames[::k]
    if not fr:
        return []
    end, W = fr[-1][0], cfg.LORA_WINDOW
    starts, s = [], 0.0
    while s + W <= end + 1e-6:
        starts.append(s)
        s += step
    last = max(0.0, end - W + 1.0)
    if not starts or starts[-1] < last - 1e-6:
        starts.append(last)  # последнее окно прижато к концу видео
    res = []
    for s in starts:
        w = [(t, f) for t, f in fr if s <= t < s + W]
        if w:
            res.append((s, w))
    return res


def lora_messages(window):
    return [{"role": "user", "content": _frames_content(window, cfg.LORA_SCALE)
             + [{"type": "text", "text": cfg.build_lora_prompt()}]}]


def _lora_result(frames, raw, progress=None):
    wins = lora_windows(frames, cfg.LORA_INFER_STEP)
    convs = [lora_messages(w) for _, w in wins]
    answers = []
    for i in range(0, len(convs), cfg.LORA_BATCH):
        _report(progress, "lora", 70 + 25 * i / max(1, len(convs)),
                f"Проверка дообученной моделью: окно {i}/{len(convs)}")
        answers += _ask_batch(convs[i:i + cfg.LORA_BATCH], cfg.LORA_MAX_TOKENS)
    found = []
    for (s, w), a in zip(wins, answers):
        raw.append({"window": [s, s + cfg.LORA_WINDOW], "answer": a})
        for item in _items(_parse_json(a)):
            tid = _norm_id(item.get("id"))
            if tid is None or tid == cfg.OTHER_ID or tid in cfg.VERDICT_EXCLUDE:
                continue
            t = _to_float(item.get("time_sec"), s + cfg.LORA_WINDOW / 2)
            found.append({"id": tid, "t": min(max(t, w[0][0]), w[-1][0])})
    # одно и то же нарушение из соседних окон -> одно событие (время = медиана)
    clusters = []
    for e in sorted(found, key=lambda e: e["t"]):
        for c in clusters:
            if c["id"] == e["id"] and abs(c["ts"][-1] - e["t"]) < cfg.MERGE_SEC:
                c["ts"].append(e["t"])
                break
        else:
            clusters.append({"id": e["id"], "ts": [e["t"]]})
    violations = []
    for c in clusters:
        if len(c["ts"]) < cfg.LORA_MIN_VOTES:
            continue
        ts = sorted(c["ts"])
        main_t, ev = _evidence(frames, ts[len(ts) // 2])
        violations.append({"type": cfg.type_name(c["id"]), "timecode_sec": round(main_t, 1),
                           "description": rules.render_description(c["id"], main_t, None),
                           "evidence_frames": ev})
    violations.sort(key=lambda v: v["timecode_sec"])
    return {"verdict": "violation" if violations else "no_violation", "violations": violations}


# ---------------------------------------------------------------- объединение

def merge_results(primary, secondary, mode=None):
    """primary — lora (точнее по типу), secondary — правила (checklist). Режим — ml_config.ENSEMBLE_MODE."""
    mode = mode or cfg.ENSEMBLE_MODE
    out = list(primary["violations"])
    if mode == "fallback" and out:
        secondary = {"violations": []}
    for v in secondary["violations"]:
        if mode == "suppress_near" and any(abs(o["timecode_sec"] - v["timecode_sec"]) < cfg.ENSEMBLE_SUPPRESS_SEC
                                           for o in primary["violations"]):
            continue  # рядом есть нарушение от LoRA — доверяем её типу
        if all(not (o["type"] == v["type"] and abs(o["timecode_sec"] - v["timecode_sec"]) < cfg.MERGE_SEC)
               for o in out):
            out.append(v)
    out.sort(key=lambda v: v["timecode_sec"])
    out = out[:cfg.MAX_RULE_VIOLATIONS]
    return {"verdict": "violation" if out else "no_violation", "violations": out}


# ---------------------------------------------------------------- главная функция

def analyze(video_path, return_raw=False, use_cache=False, progress=None):
    """use_cache=True только для прогона датасета (кэш по имени файла!). Для API — False.
    progress(stage, percent, message) — необязательный обработчик этапов (для отображения в интерфейсе)."""
    _report(progress, "frames", 2, "Нарезка видео на кадры")
    frames = extract_frames(video_path, cfg.SAMPLE_FPS)
    raw = []
    if cfg.PIPELINE == "checklist":
        cached = load_cached_obs(video_path) if use_cache else None
        if cached:
            roles, obs = cached["roles"], cached["obs"]
        else:
            roles, obs = observe(frames, progress)
            if use_cache:
                p = cache_path(video_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                json.dump({"settings": _settings_key(), "roles": roles, "obs": obs},
                          open(p, "w", encoding="utf-8"), ensure_ascii=False)
        _report(progress, "detector", 62, "Детектор предметов")
        det = detections_for(video_path, frames, obs, use_cache)
        raw.append({"roles": roles, "obs": obs, "det": det})
        _report(progress, "rules", 96, "Правила и формирование результата")
        result = result_from_obs(frames, obs, det)
    elif cfg.PIPELINE == "lora":
        result = _lora_result(frames, raw)
    elif cfg.PIPELINE == "ensemble":
        get_model()
        with _base_only():
            cached = load_cached_obs(video_path) if use_cache else None
            if cached:
                roles, obs = cached["roles"], cached["obs"]
            else:
                roles, obs = observe(frames, progress)
                if use_cache:
                    p = cache_path(video_path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    json.dump({"settings": _settings_key(), "roles": roles, "obs": obs},
                              open(p, "w", encoding="utf-8"), ensure_ascii=False)
        _report(progress, "detector", 62, "Детектор предметов")
        det = detections_for(video_path, frames, obs, use_cache)
        raw.append({"roles": roles, "obs": obs, "det": det})
        lora_res = _lora_result(frames, raw, progress)
        raw.append({"lora_result": lora_res})  # отдельно, чтобы перебирать способ объединения без модели
        _report(progress, "rules", 96, "Правила и формирование результата")
        result = merge_results(lora_res, result_from_obs(frames, obs, det))
    else:
        result = _llm_result(frames, raw)
    _report(progress, "done", 100, "Готово")
    return (result, raw) if return_raw else result
