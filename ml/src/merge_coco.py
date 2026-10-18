"""
merge_coco.py — объединяет несколько экспортов Roboflow (COCO JSON) с разметкой ОДНИХ И ТЕХ ЖЕ кадров
в один датасет. Например: предметы размечены в одном аккаунте, действия и люди — в другом.

    python src/merge_coco.py data/det_objects data/det_actions --out data/det_dataset

Каждая входная папка — распакованный экспорт (внутри train/, valid/, test/ с _annotations.coco.json).
Кадры сопоставляются по исходному имени файла (X-11_t018.0.jpg), даже если Roboflow дописал к нему
.rf.<код>. Если кадр в разных экспортах разного размера, рамки пересчитываются.
Результат: <out>/train/ (все кадры и все рамки). Проверочную часть train_detector.py выделит сам.
"""
import argparse
import json
import re
import shutil
from pathlib import Path


def key_of(file_name):
    """Исходное имя кадра без добавок Roboflow: X-11_t018.0.jpg, X-11_t018-0_jpg.rf.ab12.jpg -> x11t0180."""
    name = Path(file_name).name
    name = name.split(".rf.")[0]
    name = re.sub(r"[._-](jpe?g|png)$", "", name, flags=re.I)
    name = re.sub(r"\.(jpe?g|png)$", "", name, flags=re.I)
    return re.sub(r"[^0-9a-z]", "", name.lower())


def load_export(folder):
    items = {}
    for ann_file in Path(folder).rglob("*.json"):
        data = json.load(open(ann_file, encoding="utf-8"))
        if "images" not in data or "annotations" not in data:
            continue
        cats = {c["id"]: c["name"] for c in data.get("categories", [])}
        by_img = {}
        for a in data["annotations"]:
            by_img.setdefault(a["image_id"], []).append(a)
        for im in data["images"]:
            path = ann_file.parent / im["file_name"]
            if not path.exists():
                continue
            k = key_of(im["file_name"])
            entry = items.setdefault(k, {"path": path, "w": im["width"], "h": im["height"], "anns": []})
            for a in by_img.get(im["id"], []):
                entry["anns"].append({"name": cats.get(a["category_id"], "?"), "bbox": a["bbox"],
                                      "w": im["width"], "h": im["height"]})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exports", nargs="+", help="папки с распакованными экспортами Roboflow")
    ap.add_argument("--out", default="data/det_dataset")
    ap.add_argument("--union", action="store_true",
                    help="брать кадры, размеченные хотя бы в одном экспорте (по умолчанию — только во всех)")
    args = ap.parse_args()

    merged, keys_per_export = {}, []
    for folder in args.exports:
        part = load_export(folder)
        keys_per_export.append(set(part))
        n_boxes = sum(len(v["anns"]) for v in part.values())
        print(f"{folder}: кадров {len(part)}, рамок {n_boxes}")
        for k, v in part.items():
            if k not in merged:
                merged[k] = {"path": v["path"], "w": v["w"], "h": v["h"], "anns": []}
            m = merged[k]
            for a in v["anns"]:
                sx, sy = m["w"] / a["w"], m["h"] / a["h"]  # другой размер кадра — пересчёт рамки
                x, y, w, h = a["bbox"]
                m["anns"].append({"name": a["name"], "bbox": [x * sx, y * sy, w * sx, h * sy]})

    if not args.union and len(keys_per_export) > 1:
        common = set.intersection(*keys_per_export)
        dropped = len(merged) - len(common)
        merged = {k: v for k, v in merged.items() if k in common}
        print(f"Оставлены только кадры, размеченные во ВСЕХ экспортах: {len(common)} "
              f"(отброшено {dropped} — иначе на них не хватает части рамок, и детектор учится, "
              f"что, например, кассира на кадре нет)")

    names = sorted({a["name"] for v in merged.values() for a in v["anns"]})
    cat_id = {n: i + 1 for i, n in enumerate(names)}
    out = Path(args.out) / "train"
    if out.parent.exists():
        shutil.rmtree(out.parent)
    out.mkdir(parents=True)
    images, anns = [], []
    for i, (k, v) in enumerate(sorted(merged.items()), start=1):
        fname = f"{k}{v['path'].suffix.lower()}"
        shutil.copy(v["path"], out / fname)
        images.append({"id": i, "file_name": fname, "width": v["w"], "height": v["h"]})
        for a in v["anns"]:
            x, y, w, h = a["bbox"]
            anns.append({"id": len(anns) + 1, "image_id": i, "category_id": cat_id[a["name"]],
                         "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0})
    json.dump({"images": images, "annotations": anns,
               "categories": [{"id": c, "name": n} for n, c in cat_id.items()]},
              open(out / "_annotations.coco.json", "w", encoding="utf-8"), ensure_ascii=False)
    counts = {n: sum(1 for a in anns if a["category_id"] == c) for n, c in cat_id.items()}
    print(f"\nИтого кадров: {len(images)}, рамок: {len(anns)}")
    print("Рамок по классам:", counts)
    only_one = len(merged) - sum(1 for v in merged.values() if v["anns"])
    if only_one:
        print(f"Кадров без единой рамки: {only_one} (это нормально — они учат детектор «здесь ничего нет»)")
    print(f"Готово: {out}")


if __name__ == "__main__":
    main()
