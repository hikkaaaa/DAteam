"""
train_detector.py — обучение детектора предметов (RT-DETR, Apache 2.0) на разметке из Roboflow.

Разметка: Roboflow -> Export -> "COCO JSON" -> zip. Распаковать, например, в data/det_dataset:
    data/det_dataset/train/_annotations.coco.json + картинки
    data/det_dataset/valid/_annotations.coco.json + картинки   (если нет — 15% train уйдёт на проверку)

    python src/train_detector.py --dataset data/det_dataset             # обучение (~10–20 мин)
    python src/train_detector.py --dataset data/det_dataset --epochs 2  # быстрая проверка, что всё работает
    python src/train_detector.py --dataset data/det_people --out outputs/detector_people   # второй детектор (люди)

Результат: outputs/detector/ (модель + label_map.json). Его подхватывает detector.py.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ml_config as cfg  # noqa: E402

BASE_CHECKPOINTS = ["PekingU/rtdetr_v2_r50vd", "PekingU/rtdetr_r50vd"]


def load_split(folder):
    """[{"path", "anns": [{"bbox": [x, y, w, h], "category_id": ...}]}], {cat_id: name}"""
    folder = Path(folder)
    ann_file = folder / "_annotations.coco.json"
    if not ann_file.exists():
        cands = list(folder.glob("*.json"))
        if not cands:
            return [], {}
        ann_file = cands[0]
    data = json.load(open(ann_file, encoding="utf-8"))
    cats = {c["id"]: c["name"] for c in data["categories"]}
    by_img = {}
    for a in data["annotations"]:
        by_img.setdefault(a["image_id"], []).append(a)
    items = []
    for im in data["images"]:
        p = folder / im["file_name"]
        if p.exists():
            items.append({"path": p, "anns": by_img.get(im["id"], [])})
    return items, cats


def build_label_map(items, cats):
    """Только классы, у которых есть рамки (Roboflow добавляет пустой класс-«надкатегорию»)."""
    used = sorted({a["category_id"] for it in items for a in it["anns"]})
    names = [cats[c] for c in used]
    return {c: i for i, c in enumerate(used)}, names


def to_target(item, cat2idx, image_id, flip, img_w):
    anns = []
    for a in item["anns"]:
        if a["category_id"] not in cat2idx:
            continue
        x, y, w, h = a["bbox"]
        if w < 1 or h < 1:
            continue
        if flip:
            x = img_w - x - w
        anns.append({"bbox": [x, y, w, h], "category_id": cat2idx[a["category_id"]],
                     "area": w * h, "iscrowd": 0})
    return {"image_id": image_id, "annotations": anns}


def evaluate(model, processor, items, cat2idx, names, device, thr=0.5):
    """Простая проверка: для каждого класса найдено/пропущено/лишнее при IoU ≥ 0.5."""
    import torch
    from PIL import Image

    def iou(a, b):
        ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
        iw = max(0, min(ax2, bx2) - max(a[0], b[0]))
        ih = max(0, min(ay2, by2) - max(a[1], b[1]))
        inter = iw * ih
        return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-9)

    stats = {n: [0, 0, 0] for n in names}  # tp, fn, fp
    model.eval()
    with torch.no_grad():
        for it in items:
            img = Image.open(it["path"]).convert("RGB")
            inp = processor(images=img, return_tensors="pt").to(device)
            out = model(**inp)
            res = processor.post_process_object_detection(
                out, threshold=thr, target_sizes=torch.tensor([[img.height, img.width]], device=device))[0]
            preds = [(int(l), [float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])])
                     for l, b in zip(res["labels"], res["boxes"])]
            gts = [(cat2idx[a["category_id"]], a["bbox"]) for a in it["anns"] if a["category_id"] in cat2idx]
            used = set()
            for gl, gb in gts:
                best = None
                for j, (pl, pb) in enumerate(preds):
                    if j not in used and pl == gl and iou(gb, pb) >= 0.5:
                        best = j
                        break
                if best is None:
                    stats[names[gl]][1] += 1
                else:
                    used.add(best)
                    stats[names[gl]][0] += 1
            for j, (pl, _) in enumerate(preds):
                if j not in used:
                    stats[names[pl]][2] += 1
    model.train()
    f1s = []
    for n, (tp, fn, fp) in stats.items():
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        if tp + fn:
            f1s.append(f1)
        print(f"   {n:<18} найдено {tp:>3}  пропущено {fn:>3}  лишних {fp:>3}  | точность {p:.2f} полнота {r:.2f}")
    return sum(f1s) / len(f1s) if f1s else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="data/det_dataset")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--eval-every", type=int, default=5)
    ap.add_argument("--out", default=None, help="куда сохранить (по умолчанию outputs/detector; "
                                                "для детектора людей — outputs/detector_people)")
    args = ap.parse_args()

    import torch
    from PIL import Image, ImageEnhance
    from transformers import AutoImageProcessor, AutoModelForObjectDetection

    ds = Path(args.dataset)
    train, cats = load_split(ds / "train")
    valid, vcats = load_split(ds / "valid")
    if not train:
        sys.exit(f"Нет разметки в {ds / 'train'} — проверьте путь к распакованному экспорту COCO из Roboflow")
    cats = {**vcats, **cats}
    if not valid:
        random.Random(0).shuffle(train)
        k = max(1, len(train) * 15 // 100)
        valid, train = train[:k], train[k:]
    cat2idx, names = build_label_map(train + valid, cats)
    print(f"Картинок: обучение {len(train)}, проверка {len(valid)}. Классы: {names}")

    processor, model, last_err = None, None, None
    for ckpt in BASE_CHECKPOINTS:
        try:
            processor = AutoImageProcessor.from_pretrained(ckpt)
            model = AutoModelForObjectDetection.from_pretrained(
                ckpt, id2label=dict(enumerate(names)), label2id={n: i for i, n in enumerate(names)},
                ignore_mismatched_sizes=True)
            print("Базовая модель:", ckpt)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if model is None:
        sys.exit(f"Не удалось загрузить RT-DETR: {last_err}")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).train()

    backbone = [p for n, p in model.named_parameters() if "backbone" in n]
    other = [p for n, p in model.named_parameters() if "backbone" not in n]
    opt = torch.optim.AdamW([{"params": other, "lr": args.lr},
                             {"params": backbone, "lr": args.lr / 10}], weight_decay=1e-4)

    out_dir = Path(args.out) if args.out else Path(cfg.DETECTOR_PATH)
    out_dir.mkdir(parents=True, exist_ok=True)
    best, t0, rnd = -1.0, time.time(), random.Random(1)
    for epoch in range(1, args.epochs + 1):
        rnd.shuffle(train)
        losses = []
        for i in range(0, len(train), args.batch):
            imgs, targets = [], []
            for j, it in enumerate(train[i:i + args.batch]):
                img = Image.open(it["path"]).convert("RGB")
                flip = rnd.random() < 0.5
                if flip:
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                img = ImageEnhance.Brightness(img).enhance(rnd.uniform(0.75, 1.25))
                imgs.append(img)
                targets.append(to_target(it, cat2idx, i + j, flip, img.width))
            enc = processor(images=imgs, annotations=targets, return_tensors="pt")
            labels = [{k: v.to(device) for k, v in t.items()} for t in enc["labels"]]
            out = model(pixel_values=enc["pixel_values"].to(device), labels=labels)
            opt.zero_grad(set_to_none=True)
            out.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.1)
            opt.step()
            losses.append(out.loss.item())
        print(f"эпоха {epoch}/{args.epochs} loss {sum(losses) / len(losses):.3f} | {time.time() - t0:.0f} с", flush=True)
        if epoch % args.eval_every == 0 or epoch == args.epochs:
            print(" проверка:")
            f1 = evaluate(model, processor, valid, cat2idx, names, device)
            print(f" средний F1 {f1:.3f}")
            if f1 > best:
                best = f1
                model.save_pretrained(out_dir)
                processor.save_pretrained(out_dir)
                json.dump({"names": names}, open(out_dir / "label_map.json", "w", encoding="utf-8"),
                          ensure_ascii=False)
                print(f" сохранено (лучший F1 {best:.3f}) -> {out_dir}")
    print(f"Готово за {time.time() - t0:.0f} с. Лучший средний F1 {best:.3f}")


if __name__ == "__main__":
    main()
