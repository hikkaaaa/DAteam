"""
run_dataset.py — прогоняет видео через analyze() и сохраняет ответы для evaluate.py.

    python src/run_dataset.py --video data/X-1.mov     # один ролик
    python src/run_dataset.py                          # весь датасет (ответы модели кэшируются)
    python src/run_dataset.py --rules-only             # БЕЗ модели: заново применить правила к кэшу
                                                       # (после правки RULE_PARAMS / rules.py — секунды)
Потом:
    python src/evaluate.py

Сохраняет:
    outputs/preds/<видео>.json  — ответ в формате ТЗ (+ processing_sec)
    outputs/raw/<видео>.json    — сырые ответы модели
    outputs/obs/<видео>.json    — кэш ответов по кадрам (режим checklist)
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import model  # noqa: E402
from frames import extract_frames  # noqa: E402
import ml_config as cfg  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--video", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="outputs/preds")
    ap.add_argument("--raw", default="outputs/raw")
    ap.add_argument("--rules-only", action="store_true", help="не запускать модель, применить правила к кэшу")
    ap.add_argument("--no-cache", action="store_true", help="не использовать кэш, спросить модель заново")
    ap.add_argument("--only", default=None, help="'val' или список видео через запятую")
    ap.add_argument("--lora-preds", default="outputs/lora_part",
                    help="ensemble + --rules-only: где лежат готовые ответы lora")
    args = ap.parse_args()

    videos = [Path(args.video)] if args.video else sorted(
        p for p in Path(args.data).iterdir() if p.suffix.lower() == ".mov")
    if args.only:
        from evaluate import norm_name, parse_only
        only = parse_only(args.only)
        videos = [p for p in videos if norm_name(p.name) in only]
    if args.limit:
        videos = videos[: args.limit]
    out_dir, raw_dir = Path(args.out), Path(args.raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not args.rules_only:
        t0 = time.time()
        model.get_model()
        print(f"Модель загружена за {time.time() - t0:.0f} с. Режим: {cfg.PIPELINE}. Видео: {len(videos)}")

    for p in videos:
        t0 = time.time()
        try:
            if args.rules_only:
                cached = model.load_cached_obs(p)
                if not cached:
                    print(f"{p.name}: нет кэша (запустите без --rules-only)")
                    continue
                frames = extract_frames(p, cfg.SAMPLE_FPS)
                det = model.detections_for(p, frames, cached["obs"], use_cache=True)
                result = model.result_from_obs(frames, cached["obs"], det)
                if cfg.PIPELINE == "ensemble":
                    lora_pred = Path(args.lora_preds) / f"{p.stem}.json"
                    if lora_pred.exists():
                        result = model.merge_results(json.load(open(lora_pred, encoding="utf-8")), result)
                raw = None
            else:
                result, raw = model.analyze(p, return_raw=True, use_cache=not args.no_cache)
        except Exception as e:  # noqa: BLE001
            print(f"{p.name}: ОШИБКА {e}")
            continue
        dt = time.time() - t0
        if not args.rules_only:
            result["processing_sec"] = round(dt, 1)
            with open(raw_dir / f"{p.stem}.json", "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=1)
            lora_part = next((r["lora_result"] for r in raw if isinstance(r, dict) and "lora_result" in r), None)
            if lora_part is not None:  # ответ одной LoRA — для --rules-only в режиме ensemble
                lp = Path(args.lora_preds)
                lp.mkdir(parents=True, exist_ok=True)
                with open(lp / f"{p.stem}.json", "w", encoding="utf-8") as f:
                    json.dump(lora_part, f, ensure_ascii=False)
        else:  # сохранить время обработки из прошлого прогона, если было
            old = out_dir / f"{p.stem}.json"
            if old.exists():
                prev = json.load(open(old, encoding="utf-8")).get("processing_sec")
                if prev is not None:
                    result["processing_sec"] = prev
        with open(out_dir / f"{p.stem}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        found = ", ".join(f"{v['type']} @ {v['timecode_sec']}s" for v in result["violations"]) or "-"
        print(f"{p.name}: {result['verdict']} | {found} | {dt:.0f} с")


if __name__ == "__main__":
    main()
