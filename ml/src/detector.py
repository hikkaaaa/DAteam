"""
detector.py — детектор предметов (RT-DETR, обучен train_detector.py) для режимов checklist/ensemble.

Детектор отвечает на «предметные» вопросы вместо Qwen (деньги, ключи, печать, телефон, сумка,
сейф, дверь). Qwen остаётся для людей и действий (кассир на месте, клиент, передача денег, пересчёт).
Если папки outputs/detector нет — всё работает как раньше, без детектора.
"""
import json
from pathlib import Path

import ml_config as cfg

_dets = None  # [(model, processor, names, device), ...]


def _paths():
    paths = getattr(cfg, "DETECTOR_PATHS", [cfg.DETECTOR_PATH])
    return [Path(p) for p in paths if (Path(p) / "label_map.json").exists()]


def available():
    return cfg.USE_DETECTOR and bool(_paths())


def stamp():
    """Метка версии детекторов (для кэша): меняется, если какой-то детектор переобучили."""
    return round(sum((p / "label_map.json").stat().st_mtime for p in _paths()), 3) + getattr(cfg, "DET_CACHE_MIN", 0)


def _load():
    global _dets
    if _dets is None:
        import torch
        from transformers import AutoImageProcessor, AutoModelForObjectDetection
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _dets = []
        for path in _paths():
            names = json.load(open(path / "label_map.json", encoding="utf-8"))["names"]
            model = AutoModelForObjectDetection.from_pretrained(path).to(device).eval()
            processor = AutoImageProcessor.from_pretrained(path)
            _dets.append((model, processor, names, device))
    return _dets


def detect(pil_images, batch=16):
    """Для каждой картинки: {класс: [уверенность, ...]} — ответы всех детекторов вместе."""
    import torch
    out = [dict() for _ in pil_images]
    thr_min = min(getattr(cfg, "DET_CACHE_MIN", 0.3), min(cfg.DET_THRESHOLDS.values()))
    for model, processor, names, device in _load():
        for i in range(0, len(pil_images), batch):
            imgs = pil_images[i:i + batch]
            inp = processor(images=imgs, return_tensors="pt").to(device)
            with torch.no_grad():
                res = model(**inp)
            sizes = torch.tensor([[im.height, im.width] for im in imgs], device=device)
            for j, r in enumerate(processor.post_process_object_detection(res, threshold=thr_min,
                                                                          target_sizes=sizes)):
                for score, label in zip(r["scores"].tolist(), r["labels"].tolist()):
                    out[i + j].setdefault(names[label], []).append(round(score, 3))
    return out


def _n(name):
    """Название класса без учёта регистра и пробелов: "Safe open" == "safe_open"."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def class_names():
    names = set()
    for p in _paths():
        names |= set(json.load(open(p / "label_map.json", encoding="utf-8"))["names"])
    return names


def apply(obs, det, names=None):
    """Заменяет флаги в ответах Qwen на ответы детектора. det — [{"t", "found"}].
    Флаг меняется, только если детектор обучен хотя бы на одном его классе."""
    if not det:
        return obs
    names = {_n(x) for x in (class_names() if names is None else names)}
    flag_map = {flag: {_n(c) for c in cls} for flag, cls in cfg.DET_FLAG_MAP.items()}
    flag_map = {flag: cls for flag, cls in flag_map.items() if names & cls}
    count_map = {flag: ({_n(c) for c in cls}, k) for flag, (cls, k) in getattr(cfg, "DET_COUNT_FLAGS", {}).items()}
    count_map = {flag: v for flag, v in count_map.items() if names & v[0]}
    by_t = {round(d["t"], 2): d["found"] for d in det}
    new = []
    for o in obs:
        found = by_t.get(round(o["t"], 2))
        if found is None:
            new.append(o)
            continue
        f = dict(o["f"])
        found_n = {}
        thr = {_n(k): v for k, v in cfg.DET_THRESHOLDS.items()}
        for c, scores in found.items():
            t = thr.get(_n(c), cfg.DET_THRESHOLDS["default"])
            kept = [x for x in scores if x >= t]  # порог применяется здесь, поэтому его можно подбирать
            if kept:
                found_n.setdefault(_n(c), []).extend(kept)
        for flag, classes in flag_map.items():
            f[flag] = 1 if any(c in found_n for c in classes) else 0
        for flag, (classes, k) in count_map.items():
            people = sum(len(found_n.get(c, [])) for c in classes)
            f[flag] = 1 if people >= k and not f.get("collector_present") else 0
        money = [c for c in (_n("Money"), _n("cash")) if c in names]
        if money:
            n_stacks = sum(len(found_n.get(c, [])) for c in money)
            f["several_cash_stacks"] = 1 if n_stacks >= getattr(cfg, "DET_STACKS_MIN", 2) else 0
        new.append({"t": o["t"], "f": f})
    return new
