"""
fix_ls_export.py — собирает датасет для детектора из экспорта Label Studio (COCO), когда картинки
в экспорт не попали или называются иначе (Label Studio пишет пути вида /data/upload/1/<код>-Х-5_t000.0.jpg).

Картинки берутся из папки кадров на сервере (outputs/label_frames), сопоставление — по исходному имени
кадра без кода Label Studio, с учётом русской/латинской Х.

    python src/fix_ls_export.py result.json --frames outputs/label_frames --out data/det_people
Результат: <out>/train/_annotations.coco.json + картинки. Дальше:
    python src/train_detector.py --dataset data/det_people --out outputs/detector_people
"""
import argparse
import json
import re
import shutil
import urllib.parse
from pathlib import Path


def key(name):
    """'945b91ed-Х-5_t000.0.jpg' -> 'x-5_t000.0' (без кода Label Studio, Х -> x, без расширения)."""
    name = re.split(r"[\\/]", urllib.parse.unquote(str(name)))[-1]   # пути и с / и с \\ (Windows)
    name = re.sub(r"^[0-9a-f]{8}-", "", name)          # код, который добавляет Label Studio
    name = name.replace("Х", "X").replace("х", "x")
    return re.sub(r"\.(jpe?g|png)$", "", name, flags=re.I).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result_json")
    ap.add_argument("--frames", default="outputs/label_frames", help="папка с исходными кадрами")
    ap.add_argument("--extra-images", default=None, help="доп. папка с картинками (например images/ из экспорта)")
    ap.add_argument("--out", default="data/det_people")
    args = ap.parse_args()

    d = json.load(open(args.result_json, encoding="utf-8"))
    pool = {}
    for folder in filter(None, [args.frames, args.extra_images]):
        for p in Path(folder).glob("*"):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                pool.setdefault(key(p.name), p)

    out = Path(args.out) / "train"
    if out.parent.exists():
        shutil.rmtree(out.parent)
    out.mkdir(parents=True)

    keep_ids, images, missing = set(), [], []
    for im in d["images"]:
        k = key(im["file_name"])
        src = pool.get(k)
        if src is None:
            missing.append(re.split(r"[\\/]", urllib.parse.unquote(im["file_name"]))[-1])
            continue
        new_name = f"{k}{src.suffix.lower()}"
        shutil.copy(src, out / new_name)
        images.append({**im, "file_name": new_name})
        keep_ids.add(im["id"])
    anns = [a for a in d["annotations"] if a["image_id"] in keep_ids]
    json.dump({"images": images, "annotations": anns, "categories": d["categories"]},
              open(out / "_annotations.coco.json", "w", encoding="utf-8"), ensure_ascii=False)

    names = {c["id"]: c["name"] for c in d["categories"]}
    counts = {}
    for a in anns:
        counts[names[a["category_id"]]] = counts.get(names[a["category_id"]], 0) + 1
    print(f"Кадров в разметке: {len(d['images'])}, найдено картинок: {len(images)}, не найдено: {len(missing)}")
    print("Рамок по классам:", counts)
    if missing:
        print("Не найдены (первые 10):", missing[:10])
    print(f"Готово: {out}")


if __name__ == "__main__":
    main()
