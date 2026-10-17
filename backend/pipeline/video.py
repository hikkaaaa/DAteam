"""Video I/O: frame sampling with real timestamps, frame lookup, JPEG encoding."""

import base64
import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
from PIL import Image


@dataclass
class Frame:
    timestamp_sec: float  # real timestamp from the container, not index / fps
    image: np.ndarray     # RGB, HxWx3, uint8


def iter_frames(video_path: Path, fps: float, max_side_px: int | None = None,
                start_sec: float | None = None, end_sec: float | None = None) -> Iterator[Frame]:
    """Stream frames at ~`fps` (never loads the whole video into memory).
    `start_sec` / `end_sec` limit decoding to a segment; timestamps stay relative to the full video."""
    step, next_ts = 1.0 / fps, start_sec or 0.0
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        if start_sec:
            container.seek(int(start_sec / stream.time_base), stream=stream, backward=True)
        for frame in container.decode(stream):
            if end_sec is not None and frame.time is not None and frame.time > end_sec + 1e-6:
                break
            if frame.time is None or frame.time + 1e-6 < next_ts:
                continue
            yield Frame(float(frame.time), _to_rgb(frame, max_side_px))
            while next_ts <= frame.time + 1e-6:  # stay on a fixed sampling grid
                next_ts += step


def duration_sec(video_path: Path) -> float:
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        if stream.duration is not None:
            return float(stream.duration * stream.time_base)
        return (container.duration or 0) / av.time_base


def read_frames_at(video_path: Path, timestamps: list[float], max_side_px: int | None = None) -> list[Frame]:
    """Decode the frames closest to the given timestamps (evidence images, VLM checks)."""
    frames = []
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        for ts in sorted(timestamps):
            container.seek(int(ts / stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                if frame.time is not None and frame.time + 1e-6 >= ts:
                    frames.append(Frame(float(frame.time), _to_rgb(frame, max_side_px)))
                    break
    return frames


def encode_jpeg(image: np.ndarray, quality: int) -> str:
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _to_rgb(frame: av.VideoFrame, max_side_px: int | None) -> np.ndarray:
    image = frame.to_image()
    if max_side_px and max(image.size) > max_side_px:
        image.thumbnail((max_side_px, max_side_px))
    return np.asarray(image)
