"""Offline evaluation against the jury criteria (CLAUDE.md, "Валидация и метрики").

Runs the pipeline on every video of a split and compares with ground-truth labels:
  - verdict per video: accuracy, precision, recall
  - timecode accuracy: share of matched events with |Δt| <= 2 s and <= 5 s
  - violation type accuracy (on time-matched events)
  - processing time per video

Results are written to reports/<timestamp>.json — compare with the previous run
after every model or threshold change.

Usage (from the repo root):
    python scripts/evaluate.py --videos data/val --labels data/val/labels.json
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pipeline.runner import analyze_video  # noqa: E402
from rules import load_violation_types  # noqa: E402

# TODO(data): agree on the label format once the dataset arrives. Assumed for now:
#   { "video_001.mov": [ {"type": "V01", "timecode_sec": 44.2}, ... ],  "video_002.mov": [] }
# Split by VIDEO (never by frame); keep other camera angles in a separate split.
# Never evaluate or train on the jury test videos.


def match_events(predicted: list[dict], expected: list[dict], max_dt: float) -> list[tuple[dict, dict]]:
    """Greedy 1:1 matching of predicted to expected events by closest timecode."""
    pairs, used = [], set()
    for exp in expected:
        best, best_dt = None, max_dt
        for i, pred in enumerate(predicted):
            dt = abs(pred["timecode_sec"] - exp["timecode_sec"])
            if i not in used and dt <= best_dt:
                best, best_dt = i, dt
        if best is not None:
            used.add(best)
            pairs.append((predicted[best], exp))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()

    labels: dict[str, list[dict]] = json.loads(args.labels.read_text(encoding="utf-8"))
    type_id_by_name = {t.type: t.id for t in load_violation_types().values()}

    tp = fp = fn = tn = 0
    matched_2s = matched_5s = type_correct = total_expected = 0
    times: list[float] = []

    for video_name, expected in labels.items():
        started = time.perf_counter()
        violations = analyze_video(args.videos / video_name)
        times.append(time.perf_counter() - started)

        predicted = [{"type": type_id_by_name.get(v.type), "timecode_sec": v.timecode_sec} for v in violations]

        has_pred, has_true = bool(predicted), bool(expected)
        tp += has_pred and has_true
        fp += has_pred and not has_true
        fn += not has_pred and has_true
        tn += not has_pred and not has_true

        total_expected += len(expected)
        pairs_5s = match_events(predicted, expected, 5.0)
        matched_5s += len(pairs_5s)
        matched_2s += len(match_events(predicted, expected, 2.0))
        type_correct += sum(p["type"] == e["type"] for p, e in pairs_5s)
        # TODO(data): for every FP/FN save frames with drawn detections to reports/errors/.

    n = max(len(labels), 1)
    report = {
        "verdict": {
            "accuracy": (tp + tn) / n,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        },
        "timecode": {
            "within_2s": matched_2s / total_expected if total_expected else None,
            "within_5s": matched_5s / total_expected if total_expected else None,
        },
        "type_accuracy": type_correct / matched_5s if matched_5s else None,
        "processing_sec": {"mean": sum(times) / n, "max": max(times, default=0)},
        "videos": len(labels),
    }

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    out = reports_dir / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nSaved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
