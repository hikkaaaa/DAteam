"""
frames.py — извлечение кадров из видео с точным временем.

Примеры запуска (из папки C:\\datathon, с включённым venv):
    python src\\frames.py data\\X-1.mov
    python src\\frames.py data\\X-1.mov --fps 2 --check 22

Что делает:
  1. Печатает параметры видео (разрешение, FPS, длительность).
  2. Берёт N кадров в секунду (по умолчанию 2) и сохраняет их в outputs\\<имя видео>_frames\\
     Имя файла = точное время кадра в секундах, например t_0012.483.jpg
  3. С флагом --check сохраняет один кадр на указанной секунде (для сверки с разметкой).

Время кадра берётся из самого видео, а не считается как "номер кадра / 30",
потому что у видео с телефона FPS "плавает" (например 29.92), и таймкод бы съезжал.
"""
import argparse
import base64
import time
from pathlib import Path

import cv2


def video_info(path):
    """Основные параметры видео."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    info = {
        "fps": round(fps, 3),
        "frames": n,
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "duration_sec": round(n / fps, 2) if fps else 0.0,
    }
    cap.release()
    return info


def _iter_frames(path):
    """Перебирает все кадры видео по порядку: (время_в_секундах, кадр)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t <= 0 and idx > 0:
                # некоторые сборки OpenCV не отдают время — тогда считаем по номеру кадра
                t = idx / native_fps
            yield t, frame
            idx += 1
    finally:
        cap.release()


def extract_frames(path, sample_fps=2.0):
    """Список (время, кадр) с частотой sample_fps кадров в секунду."""
    step = 1.0 / sample_fps
    next_t = 0.0
    result = []
    for t, frame in _iter_frames(path):
        if t + 1e-6 >= next_t:
            result.append((round(t, 3), frame))
            while next_t <= t + 1e-6:
                next_t += step
    return result


def frame_at(path, t_sec):
    """Первый кадр, время которого >= t_sec. Возвращает (точное_время, кадр)."""
    last = None
    for t, frame in _iter_frames(path):
        last = (round(t, 3), frame)
        if t + 1e-6 >= t_sec:
            return last
    return last  # если t_sec больше длины видео — последний кадр


def save_jpg(path, frame, quality=90):
    """Сохраняет кадр в JPEG (работает и с русскими буквами в пути)."""
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("Не удалось закодировать кадр в JPEG")
    buf.tofile(str(path))


def to_base64_jpeg(frame, quality=85):
    """Кадр -> строка base64 JPEG (формат evidence_frames из ТЗ)."""
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("Не удалось закодировать кадр в JPEG")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def main():
    parser = argparse.ArgumentParser(description="Извлечение кадров из видео")
    parser.add_argument("video", help="путь к видео .mov")
    parser.add_argument("--fps", type=float, default=2.0, help="сколько кадров в секунду брать")
    parser.add_argument("--check", type=float, default=None, help="сохранить кадр на этой секунде")
    parser.add_argument("--out", default="outputs", help="куда сохранять")
    args = parser.parse_args()

    video = Path(args.video)
    info = video_info(video)
    print(f"Видео: {video.name}")
    print(f"  Разрешение: {info['width']}x{info['height']}")
    print(f"  FPS: {info['fps']}, кадров: {info['frames']}, длительность: {info['duration_sec']} с")

    start = time.time()
    frames = extract_frames(video, args.fps)
    elapsed = time.time() - start

    out_dir = Path(args.out) / f"{video.stem}_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    for t, frame in frames:
        save_jpg(out_dir / f"t_{t:08.3f}.jpg", frame)

    print(f"Сохранено кадров: {len(frames)} в {out_dir}")
    if frames:
        print(f"  Время первого кадра: {frames[0][0]} с, последнего: {frames[-1][0]} с")
    print(f"  Нарезка заняла: {elapsed:.1f} с")

    if args.check is not None:
        t, frame = frame_at(video, args.check)
        check_path = Path(args.out) / f"{video.stem}_check_{args.check}s.jpg"
        save_jpg(check_path, frame)
        print(f"Кадр для проверки: запрошено {args.check} с, точное время {t} с -> {check_path}")


if __name__ == "__main__":
    main()
