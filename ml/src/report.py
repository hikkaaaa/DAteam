"""
report.py — подробный отчёт по ID нарушений: что найдено верно, что пропущено, что лишнее.

    python src/report.py                              # по outputs/preds_det4
    python src/report.py --preds outputs/preds_final  # по любой папке с ответами
    python src/report.py > report.txt                 # сохранить в файл

По каждому видео:
    ВЕРНО     — тип из разметки, который сервис назвал (с секундой ответа и разметки)
    ПРОПУЩЕНО — тип из разметки, который сервис не назвал
    ЛИШНЕЕ    — тип, который сервис назвал, а в разметке этого видео его нет
Внизу — сводка по каждому ID.
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ml_config as cfg  # noqa: E402
from evaluate import load_labels, load_pred, norm_name  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="data/labels.csv")
    ap.add_argument("--preds", default="outputs/preds_det4")
    ap.add_argument("--truth", default="analyst")
    args = ap.parse_args()

    labels = load_labels(args.labels, args.truth)
    for dup in getattr(cfg, "DUPLICATES", {}):
        labels.pop(norm_name(dup), None)
    name_to_id = dict(cfg.NAME_TO_ID)
    name_to_id[cfg.OTHER_TYPE] = cfg.OTHER_ID

    stat = defaultdict(lambda: {"marked": 0, "hit": 0, "miss": 0, "extra": 0, "extra_clean": 0})
    print(f"Ответы: {args.preds}\n")
    for video in sorted(labels, key=lambda n: int(n.split("-")[1])):
        gt = labels[video]
        pred = load_pred(args.preds, video) or {"violations": []}
        pv = [(name_to_id.get(v.get("type"), v.get("type")), v.get("timecode_sec")) for v in pred["violations"]]
        gt_ids = [t for t in dict.fromkeys(gt["types"]) if t != "V11"]
        got_ids = list(dict.fromkeys(i for i, _ in pv))
        tcs = ", ".join(f"{t:g}" for t in gt["timecodes"])

        hit = [i for i in gt_ids if i in got_ids]
        miss = [i for i in gt_ids if i not in got_ids]
        extra = [(i, t) for i, t in pv if i not in gt_ids]

        for i in gt_ids:
            stat[i]["marked"] += 1
        for i in hit:
            stat[i]["hit"] += 1
        for i in miss:
            stat[i]["miss"] += 1
        for i, _ in extra:
            stat[i]["extra"] += 1
            if not gt["violation"]:
                stat[i]["extra_clean"] += 1

        if not gt["violation"]:
            head = "чисто" + (" — ЛОЖНОЕ СРАБАТЫВАНИЕ" if pv else " — OK")
        elif not pv:
            head = "нарушение — ПРОПУСК"
        else:
            head = "нарушение — найдено" + ("" if hit else " (но тип не тот)")
        print(f"{video:<6} {head}")
        if gt_ids:
            print(f"       разметка:  {', '.join(gt_ids)} @ {tcs} с")
        for i in hit:
            when = ", ".join(f"{t:g}" for j, t in pv if j == i)
            print(f"       ВЕРНО:     {i} {cfg.type_name(i)} — ответ @ {when} с")
        for i in miss:
            print(f"       ПРОПУЩЕНО: {i} {cfg.type_name(i)}")
        for i, t in extra:
            print(f"       ЛИШНЕЕ:    {i} {cfg.type_name(i) if i in cfg.TYPES or i == cfg.OTHER_ID else i} @ {t:g} с")
    print("\n" + "=" * 96)
    print("СВОДКА ПО ID")
    print(f"{'ID':<6}{'название':<52}{'размечено':>10}{'верно':>7}{'пропущ.':>9}{'лишних':>8}{'на чист.':>9}")
    for i in sorted(stat, key=lambda x: (x == cfg.OTHER_ID, x)):
        s = stat[i]
        name = cfg.type_name(i) if i in cfg.TYPES or i == cfg.OTHER_ID else i
        print(f"{i:<6}{name[:50]:<52}{s['marked']:>10}{s['hit']:>7}{s['miss']:>9}{s['extra']:>8}{s['extra_clean']:>9}")
    print("\nлишних — назван там, где этого типа нет в разметке; на чист. — из них на видео без нарушений")


if __name__ == "__main__":
    main()
