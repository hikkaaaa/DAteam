"""
train_lora.py — дообучение Qwen3-VL (LoRA) на окнах видео из датасета.

    pip install -q peft
    python src/train_lora.py --dry-run     # только собрать примеры и показать статистику (без GPU)
    python src/train_lora.py --smoke       # проверить, что обучение запускается (1–2 минуты)
    python src/train_lora.py               # обучение; проверочные видео (VAL_VIDEOS) отложены
    python src/train_lora.py --all         # финальное обучение на всех видео

Проверка после обучения (в ml_config.py PIPELINE = "lora"):
    python src/run_dataset.py --only val --out outputs/preds_lora
    python src/evaluate.py --only val --preds outputs/preds_lora

Пример = окно 8 с (8 кадров с подписанным временем) -> ответ
    {"violations": [{"id": "V14", "time_sec": 18.0}]}   или   {"violations": []}
"""
import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ml_config as cfg  # noqa: E402
from evaluate import norm_name  # noqa: E402
from frames import extract_frames  # noqa: E402

# Отложенные видео: по одному из типов, у которых в датасете ≥ 2 примера, + 2 чистых.
# (X-47 и X-48 — копии X-4 и X-5, поэтому заменены на X-18 и X-43.)
VAL_VIDEOS = ["X-18", "X-26", "X-28", "X-33", "X-37", "X-46", "X-43", "X-45"]


def load_events(path):
    """{видео: [(секунда, тип), ...]} — сопоставление таймкодов и типов из labels.csv."""
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tcs = [float(x) for x in r["timecodes"].split(";") if x.strip()]
            types = [x.strip() for x in r["types"].split(";") if x.strip()]
            types = [t for t in types if t not in cfg.VERDICT_EXCLUDE]
            if not tcs or not types:
                ev = []
            elif len(tcs) == len(types):
                ev = list(zip(tcs, types))
            elif len(types) == 1:
                ev = [(t, types[0]) for t in tcs]
            elif len(tcs) == 1:
                ev = [(tcs[0], ty) for ty in types]
            else:  # таймкодов больше, чем типов: лишние таймкоды — последнего типа
                ev = [(t, types[min(i, len(types) - 1)]) for i, t in enumerate(tcs)]
            out[norm_name(r["video"])] = ev
    # копии: их таймкоды добавляем к оригиналу, сами копии убираем
    for dup, orig in cfg.DUPLICATES.items():
        d, o = norm_name(dup), norm_name(orig)
        if d in out:
            extra = [e for e in out.pop(d) if e not in out.get(o, [])]
            out[o] = sorted(out.get(o, []) + extra)
    return out


def build_examples(data_dir, labels_path, videos_filter, neg_ratio, seed=0):
    import model as M  # тяжёлый импорт только здесь
    events = load_events(labels_path)
    files = {norm_name(p.name): p for p in Path(data_dir).iterdir() if p.suffix.lower() == ".mov"}
    pos, neg = [], []
    W = cfg.LORA_WINDOW
    for name, ev in sorted(events.items()):
        if not videos_filter(name) or name not in files:
            continue
        frames = extract_frames(files[name], cfg.SAMPLE_FPS)
        for s, w in M.lora_windows(frames, cfg.LORA_TRAIN_STEP):
            inside = sorted([(t, ty) for t, ty in ev if s + 0.5 <= t <= s + W - 0.5])
            near = [t for t, _ in ev if s - 2 <= t <= s + W + 2]
            if inside:
                target = {"violations": [{"id": ty, "time_sec": round(t, 1)} for t, ty in inside]}
                pos.append({"video": name, "start": s, "window": w, "target": target})
            elif not near:
                neg.append({"video": name, "start": s, "window": w, "target": {"violations": []}})
    rnd = random.Random(seed)
    rnd.shuffle(neg)
    neg = neg[: max(len(pos) * neg_ratio, 20)]
    return pos, neg


def encode(processor, ex):
    """Токены примера; потери считаются только на ответе модели."""
    import torch
    import model as M
    user = M.lora_messages(ex["window"])
    target = json.dumps(ex["target"], ensure_ascii=False)
    full = user + [{"role": "assistant", "content": [{"type": "text", "text": target}]}]
    common = dict(tokenize=True, return_dict=True, return_tensors="pt")
    enc = processor.apply_chat_template(full, **common)
    prompt = processor.apply_chat_template(user, add_generation_prompt=True, **common)
    ids, p_ids = enc["input_ids"], prompt["input_ids"]
    plen = p_ids.shape[1]
    if not torch.equal(ids[0, :plen], p_ids[0]):
        # запасной вариант: ищем начало ответа ассистента
        marker = processor.tokenizer.encode("<|im_start|>assistant\n", add_special_tokens=False)
        seq = ids[0].tolist()
        plen = next(i + len(marker) for i in range(len(seq) - len(marker), -1, -1)
                    if seq[i:i + len(marker)] == marker)
    labels = ids.clone()
    labels[:, :plen] = -100
    enc["labels"] = labels
    return enc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--labels", default="data/labels.csv")
    ap.add_argument("--all", action="store_true", help="обучать на всех видео (финальная модель)")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--neg-ratio", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None, help="куда сохранить адаптер (по умолчанию ml_config.LORA_PATH)")
    ap.add_argument("--smoke", action="store_true", help="быстрая проверка: 8 примеров, 1 эпоха, адаптер не сохраняется")
    args = ap.parse_args()

    val = {norm_name(v) for v in VAL_VIDEOS}
    flt = (lambda n: True) if args.all else (lambda n: n not in val)
    pos, neg = build_examples(args.data, args.labels, flt, args.neg_ratio)
    train = pos + neg
    if args.smoke:
        random.Random(0).shuffle(train)
        train, args.epochs, args.accum = train[:8], 1, 4
    by_type = {}
    for ex in pos:
        for v in ex["target"]["violations"]:
            by_type[v["id"]] = by_type.get(v["id"], 0) + 1
    print(f"Примеров: {len(train)} (с нарушением {len(pos)}, без {len(neg)}). По типам: {dict(sorted(by_type.items()))}")
    print("Проверочные видео (не в обучении):", "нет (--all)" if args.all else ", ".join(sorted(val)))
    if args.dry_run:
        return

    import torch
    from peft import LoraConfig, get_peft_model
    import model as M

    model, processor = M.get_model(with_lora=False)
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    lm_regex = r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)"
    try:
        lcfg = LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05,
                          target_modules=lm_regex, task_type="CAUSAL_LM")
        pm = get_peft_model(model, lcfg)
    except ValueError:  # другие имена модулей — берём по коротким именам
        lcfg = LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05,
                          target_modules=["q_proj", "k_proj", "v_proj", "o_proj"], task_type="CAUSAL_LM")
        pm = get_peft_model(model, lcfg)
    pm.print_trainable_parameters()
    pm.train()

    params = [p for p in pm.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    total_steps = max(1, args.epochs * len(train) // args.accum)
    warmup = max(1, total_steps // 10)
    step, t0, running = 0, time.time(), 0.0
    rnd = random.Random(1)
    for epoch in range(args.epochs):
        rnd.shuffle(train)
        for i, ex in enumerate(train):
            enc = encode(processor, ex).to(model.device)
            loss = pm(**enc).loss / args.accum
            loss.backward()
            running += loss.item()
            if (i + 1) % args.accum == 0 or i == len(train) - 1:
                for g in opt.param_groups:
                    g["lr"] = args.lr * min(1.0, (step + 1) / warmup)
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                if step % 5 == 0 or args.smoke:
                    print(f"эпоха {epoch + 1}/{args.epochs} шаг {step}/{total_steps} "
                          f"loss {running / 5:.4f} | {time.time() - t0:.0f} с", flush=True)
                    running = 0.0
    if args.smoke:
        print(f"Проверка прошла: обучение работает ({time.time() - t0:.0f} с на 8 примеров). Адаптер не сохранён.")
        return
    out = Path(args.out) if args.out else Path(cfg.LORA_PATH)
    out.mkdir(parents=True, exist_ok=True)
    pm.save_pretrained(str(out))
    (out / "val_videos.txt").write_text("" if args.all else "\n".join(sorted(val)), encoding="utf-8")
    print(f"Готово за {time.time() - t0:.0f} с. Адаптер: {out}")


if __name__ == "__main__":
    main()
