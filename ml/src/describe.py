"""
describe.py — модель подробно описывает, что происходит на фрагменте видео.
Нужен, чтобы понять, что модель видит, и на этой основе править промпты и критерии типов.

    python src/describe.py --video data/X-1.mov --start 16 --end 25
    python src/describe.py --all-violations      # каждое размеченное нарушение: [секунда-6, секунда+3]
Результат печатается и сохраняется в outputs/describe_violations.txt
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ml_config as cfg  # noqa: E402
from evaluate import norm_name  # noqa: E402
from frames import extract_frames  # noqa: E402
from model import _ask, _to_pil, get_model  # noqa: E402

PROMPT = (
    "Это кадры с камеры видеонаблюдения над банковской кассой, у каждого кадра указано время. "
    "Опиши подробно по времени, что происходит: сколько людей в кадре и где каждый находится, "
    "что у каждого в руках, какие предметы передают и кому, куда кладут деньги или ценности, "
    "уходит ли кто-то с места, открыт ли ящик, сейф или дверь. Пиши только то, что видно на кадрах. "
    "Отвечай на русском."
)


def describe(video, start, end, fps=2.0):
    frames = [(t, f) for t, f in extract_frames(video, fps) if start <= t <= end]
    if not frames:
        return "(нет кадров в этом промежутке)"
    content = []
    for t, f in frames:
        content.append({"type": "text", "text": f"[{t:.1f} с]"})
        content.append({"type": "image", "image": _to_pil(f)})
    content.append({"type": "text", "text": PROMPT})
    return _ask([{"role": "user", "content": content}])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=10.0)
    ap.add_argument("--all-violations", action="store_true")
    ap.add_argument("--data", default="data")
    ap.add_argument("--labels", default="data/labels.csv")
    ap.add_argument("--out", default="outputs/describe_violations.txt")
    args = ap.parse_args()

    cfg.MAX_NEW_TOKENS = 700
    get_model()

    if not args.all_violations:
        print(describe(args.video, args.start, args.end))
        return

    files = {norm_name(p.name): p for p in Path(args.data).iterdir() if p.suffix.lower() == ".mov"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.labels, encoding="utf-8-sig") as f_lab, open(args.out, "w", encoding="utf-8") as f_out:
        for row in csv.DictReader(f_lab):
            tcs = [float(x) for x in row["timecodes"].split(";") if x.strip()]
            if not tcs:
                continue
            name = norm_name(row["video"])
            video = files.get(name)
            if video is None:
                print(f"{name}: файл не найден")
                continue
            types = ", ".join(f"{t} ({cfg.TYPES[t]['type']})" for t in row["types"].split(";") if t in cfg.TYPES)
            official = "официально: нарушение" if row["violation"] == "1" else "официально: БЕЗ нарушения"
            for tc in tcs:
                text = describe(video, max(0.0, tc - 6), tc + 3)
                block = (f"===== {name} | {tc:g} с | {official} | аналитики: {types} =====\n"
                         f"{text}\n\n")
                print(block)
                f_out.write(block)
                f_out.flush()


if __name__ == "__main__":
    main()
