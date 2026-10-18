"""
fp_report.py — какие типы срабатывают на чистых видео (ложные) и какие попадают в настоящие нарушения.

    python src/fp_report.py                       # по outputs/preds
    python src/fp_report.py --preds outputs/preds_lora
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ml_config as cfg  # noqa: E402
from evaluate import load_labels, load_pred  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="data/labels.csv")
    ap.add_argument("--preds", default="outputs/preds")
    ap.add_argument("--truth", choices=["official", "analyst"], default="analyst")
    args = ap.parse_args()

    name_to_id = dict(cfg.NAME_TO_ID)
    labels = load_labels(args.labels, args.truth)
    on_clean = defaultdict(list)      # тип -> чистые видео, где сработал
    on_viol_hit = defaultdict(list)   # тип -> видео с нарушением, где тип совпал с разметкой
    on_viol_miss = defaultdict(list)  # тип -> видео с нарушением, где тип НЕ из разметки
    for video, gt in labels.items():
        pred = load_pred(args.preds, video)
        if not pred:
            continue
        ids = {name_to_id.get(v.get("type"), v.get("type")) for v in pred.get("violations", [])}
        for tid in ids:
            if not gt["violation"]:
                on_clean[tid].append(video)
            elif tid in gt["types"]:
                on_viol_hit[tid].append(video)
            else:
                on_viol_miss[tid].append(video)

    n_clean = sum(1 for g in labels.values() if not g["violation"])
    print(f"Чистых видео: {n_clean}\n")
    print(f"{'Тип':<5} {'на чистых':>10} {'верно':>6} {'мимо':>5}   где сработал на чистых")
    print("-" * 90)
    for tid in sorted(set(on_clean) | set(on_viol_hit) | set(on_viol_miss)):
        print(f"{tid:<5} {len(on_clean[tid]):>10} {len(on_viol_hit[tid]):>6} {len(on_viol_miss[tid]):>5}   "
              f"{', '.join(sorted(on_clean[tid]))}")
    print("\nна чистых — ложные срабатывания; верно — тип совпал с разметкой; "
          "мимо — сработал на видео с нарушением, но тип другой.")


if __name__ == "__main__":
    main()
