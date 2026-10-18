"""
evaluate.py — сравнивает ответы модели с разметкой.

Ответы модели: outputs/preds/<видео>.json (формат ТЗ), их пишет run_dataset.py.

    python src/evaluate.py --dummy                 # проверка скрипта без модели
    python src/evaluate.py                         # вердикт по разметке аналитиков (основная)
    python src/evaluate.py --truth official        # для сравнения: по исходной разметке Forte

Считает:
  - пропуски (было нарушение, модель сказала "нет") — их должно быть 0
  - ложные срабатывания (не было, модель сказала "есть")
  - ошибку таймкода (ближайший предсказанный к ближайшему размеченному)
  - угадан ли тип (хотя бы один тип совпал с разметкой аналитиков)
"""
import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def norm_name(name):
    name = Path(str(name)).stem.strip()
    if name.lower().startswith("own"):
        return name.upper()
    name = name.replace("Х", "X").replace("х", "X").replace("x", "X")
    return name.upper()


def parse_only(arg):
    """--only: 'val' (проверочные видео дообучения) или список через запятую: X-26,X-28."""
    if not arg:
        return None
    if arg == "val":
        import ml_config as cfg
        f = Path(cfg.LORA_PATH) / "val_videos.txt"
        if f.exists() and f.read_text(encoding="utf-8").strip():
            names = f.read_text(encoding="utf-8").split()
        else:
            from train_lora import VAL_VIDEOS
            names = VAL_VIDEOS
    else:
        names = arg.split(",")
    return {norm_name(n) for n in names if n.strip()}


def load_labels(path, truth):
    col = "violation" if truth == "official" else "analyst_violation"
    labels = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            labels[norm_name(row["video"])] = {
                "violation": row[col].strip() == "1",
                "timecodes": [float(x) for x in row["timecodes"].split(";") if x.strip()],
                "types": [x.strip() for x in row["types"].split(";") if x.strip()],
            }
    return labels


def load_pred(preds_dir, video):
    for p in Path(preds_dir).glob("*.json"):
        if norm_name(p.name) == video:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="data/labels.csv")
    ap.add_argument("--preds", default="outputs/preds")
    ap.add_argument("--truth", choices=["official", "analyst"], default="analyst")
    ap.add_argument("--dummy", action="store_true")
    ap.add_argument("--only", default=None, help="'val' или список видео через запятую")
    args = ap.parse_args()
    only = parse_only(args.only)

    try:
        import ml_config as cfg
        name_to_id = dict(cfg.NAME_TO_ID)
        name_to_id[cfg.OTHER_TYPE] = cfg.OTHER_ID
    except Exception:
        name_to_id = {}

    labels = {}
    for lp in args.labels.split(","):  # можно несколько файлов разметки через запятую
        labels.update(load_labels(lp, args.truth))
    try:  # копии видео (ml_config.DUPLICATES) не считаем дважды
        import ml_config as _cfg
        for dup in _cfg.DUPLICATES:
            labels.pop(norm_name(dup), None)
    except Exception:
        pass
    if only:
        labels = {k: v for k, v in labels.items() if k in only}
    tp = fn = fp = tn = missing = 0
    tc_errors, times, rows = [], [], []
    type_hits = type_total = 0
    n_pred = n_pred_ok = 0          # все выданные нарушения / из них с правильным типом
    pairs_total = pairs_found = 0   # размеченные пары (видео, тип) / найдено с правильным типом
    per_type = defaultdict(lambda: [0, 0])  # тип -> [найден, всего]

    for video, gt in labels.items():
        pred = {"verdict": "no_violation", "violations": []} if args.dummy else load_pred(args.preds, video)
        if pred is None:
            missing += 1
            rows.append((video, "НЕТ ОТВЕТА", ""))
            continue
        if "processing_sec" in pred:
            times.append(pred["processing_sec"])

        said = pred.get("verdict") == "violation"
        pv = pred.get("violations", [])
        pred_ids = {name_to_id.get(v.get("type"), v.get("type")) for v in pv}
        pred_str = ", ".join(f"{name_to_id.get(v.get('type'), v.get('type'))}@{v.get('timecode_sec')}" for v in pv)
        gt_str = ", ".join(gt["types"]) + (" @ " + ", ".join(f"{t:g}" for t in gt["timecodes"]) if gt["timecodes"] else "")

        for t in gt["types"]:
            per_type[t][1] += 1
            if t in pred_ids:
                per_type[t][0] += 1
        gt_types = [t for t in gt["types"] if t != "V11"]
        pairs_total += len(set(gt_types))
        pairs_found += len(set(gt_types) & pred_ids)
        for v in pv:
            n_pred += 1
            n_pred_ok += name_to_id.get(v.get("type"), v.get("type")) in gt_types

        if gt["violation"] and said:
            tp += 1
            det = f"разметка: {gt_str} | модель: {pred_str}"
            tcs = [v.get("timecode_sec") for v in pv if v.get("timecode_sec") is not None]
            if tcs and gt["timecodes"]:
                err = min(abs(a - b) for a in tcs for b in gt["timecodes"])
                tc_errors.append(err)
                det += f" | таймкод ±{err:.1f} с"
            if gt["types"]:
                type_total += 1
                hit = bool(pred_ids & set(gt["types"]))
                type_hits += hit
                det += " | тип OK" if hit else " | тип НЕ совпал"
            rows.append((video, "OK (нарушение найдено)", det))
        elif gt["violation"]:
            fn += 1
            rows.append((video, "ПРОПУСК", f"разметка: {gt_str}"))
        elif said:
            fp += 1
            rows.append((video, "ЛОЖНОЕ СРАБАТЫВАНИЕ", f"модель: {pred_str}" + (f" | аналитики: {gt_str}" if gt["types"] else "")))
        else:
            tn += 1
            rows.append((video, "OK (чисто)", ""))

    print(f"{'Видео':<7} {'Результат':<24} Детали")
    print("-" * 100)
    for video, res, det in rows:
        print(f"{video:<7} {res:<24} {det}")

    total = tp + fn + fp + tn
    print(f"\n===== ИТОГ (вердикт по разметке: {args.truth}) =====")
    print(f"Видео с ответом: {total}, без ответа: {missing}")
    if total:
        print(f"Верный вердикт: {tp + tn}/{total} ({100 * (tp + tn) / total:.0f}%)")
    print(f"Нарушения найдены: {tp}/{tp + fn}   ПРОПУСКИ: {fn}")
    print(f"Чистые видео верно: {tn}/{tn + fp}   ЛОЖНЫЕ СРАБАТЫВАНИЯ: {fp}")
    if type_total:
        print(f"Тип угадан: {type_hits}/{type_total} найденных нарушений")
    if n_pred:
        print(f"Точность по типам (precision): {n_pred_ok}/{n_pred} выданных нарушений с правильным типом "
              f"({100 * n_pred_ok / n_pred:.0f}%)")
    if pairs_total:
        print(f"Полнота по типам (recall): {pairs_found}/{pairs_total} размеченных пар «видео + тип» найдено "
              f"({100 * pairs_found / pairs_total:.0f}%)")
    if tc_errors:
        print(f"Таймкод: средняя ошибка {statistics.mean(tc_errors):.1f} с, медиана {statistics.median(tc_errors):.1f} с, "
              f"≤1 с: {sum(e <= 1 for e in tc_errors)}/{len(tc_errors)}, ≤2 с: {sum(e <= 2 for e in tc_errors)}/{len(tc_errors)}")
    if times:
        print(f"Время обработки: среднее {statistics.mean(times):.0f} с, максимум {max(times):.0f} с (лимит 300 с)")
    if per_type:
        print("\nПо типам (в скольких видео тип найден / сколько размечено):")
        for t in sorted(per_type):
            found, all_ = per_type[t]
            print(f"  {t}: {found}/{all_}")


if __name__ == "__main__":
    main()
