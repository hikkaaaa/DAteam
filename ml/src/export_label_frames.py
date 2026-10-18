"""
export_label_frames.py — отбирает кадры из видео для разметки предметов (Roboflow, Label Studio).

Берёт из каждого видео кадр раз в STEP секунд + кадры вокруг каждого размеченного нарушения
(там как раз видны ключи, деньги, сумки, открытый сейф и т.д.).

Запуск (на ноутбуке из C:\\datathon или на сервере из /workspace/datathon):
    python src/export_label_frames.py                 # шаг 3 с
    python src/export_label_frames.py --step 4        # меньше кадров
Результат: outputs/label_frames/*.jpg и outputs/label_frames.zip — его и загружать в Roboflow.
Имя файла: <видео>_t<секунда>.jpg, например X-11_t018.0.jpg
"""
import argparse
import csv
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import norm_name  # noqa: E402
from frames import extract_frames, save_jpg  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--labels", default="data/labels.csv")
    ap.add_argument("--out", default="outputs/label_frames")
    ap.add_argument("--step", type=float, default=3.0, help="шаг равномерной выборки, секунд")
    ap.add_argument("--around", default="-2,-1,0,1,2", help="смещения вокруг нарушения, секунд")
    args = ap.parse_args()

    tcs = {}
    with open(args.labels, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tcs[norm_name(r["video"])] = [float(x) for x in r["timecodes"].split(";") if x.strip()]
    offsets = [float(x) for x in args.around.split(",")]

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    total = 0
    for p in sorted(Path(args.data).iterdir()):
        if p.suffix.lower() != ".mov":
            continue
        name = norm_name(p.name)  # всегда латинская X в имени файла
        frames = extract_frames(p, 2.0)
        if not frames:
            continue
        end = frames[-1][0]
        wanted = [i * args.step for i in range(int(end // args.step) + 1)]
        wanted += [tc + o for tc in tcs.get(name, []) for o in offsets if 0 <= tc + o <= end]
        chosen = []
        for w in sorted(wanted):
            t, f = min(frames, key=lambda tf: abs(tf[0] - w))
            if all(abs(t - c) > 0.6 for c, _ in chosen):
                chosen.append((t, f))
        for t, f in chosen:
            save_jpg(out / f"{name}_t{t:05.1f}.jpg", f, quality=92)
        total += len(chosen)
        print(f"{name}: {len(chosen)} кадров")

    zip_path = shutil.make_archive(str(out), "zip", root_dir=out)
    print(f"\nВсего кадров: {total}\nАрхив для загрузки в Roboflow: {zip_path}")


if __name__ == "__main__":
    main()
