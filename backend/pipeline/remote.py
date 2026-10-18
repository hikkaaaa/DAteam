"""ML server client: the models (Qwen3-VL + LoRA + detector) run on the team's GPU server.

Flow (all requests carry the X-ML-Token header):
  POST {ML_URL}/infer_async  (field `file`)  -> {"ml_job_id": ...}
  GET  {ML_URL}/progress/{ml_job_id}         -> {status: queued|processing|done|failed, stage, stage_title,
                                                 percent, message, result, error}
  result = {verdict, violations} in the ТЗ format; job_id / status are added by our API (main.py).
ML stages in order: frames -> roles -> checklist -> detector -> lora -> rules -> done.
URL and token come from .env (ML_URL, ML_TOKEN); timeouts from config.yaml → ml_server.
"""

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from config import CONFIG
from pipeline.video import encode_jpeg, read_frames_at
from rules import load_norms, load_violation_types
from schemas import EvidenceFrame, Violation

# on_progress(0..1, stage, stage_title, message)
Progress = Callable[..., None]


class MLServerError(RuntimeError):
    """Message is shown to the user in the UI."""


def ml_url() -> str:
    return os.environ.get("ML_URL", "").rstrip("/")


def _headers() -> dict:
    return {"X-ML-Token": os.environ.get("ML_TOKEN", "")}


def health() -> dict:
    """{"status": "ok", ...} from the ML server, or {"status": "offline"} / {"status": "off"}."""
    if not ml_url():
        return {"status": "off"}
    try:
        return httpx.get(f"{ml_url()}/health", timeout=10).json()
    except Exception:
        return {"status": "offline"}


def prepare_video(video_path: Path, start_sec: float, end_sec: float | None,
                  roi: tuple[float, float, float, float] | None) -> Path:
    """Re-encode only [start, end] (frame-accurate) and/or crop to the cash desk zone, so the ML server
    analyzes just the selected segment / area. Timecodes stay relative to the segment start."""
    out = video_path.with_name(f"{video_path.stem}-prepared.mov")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start_sec:.2f}", "-i", str(video_path)]
    if end_sec is not None:
        cmd += ["-t", f"{end_sec - start_sec:.2f}"]
    if roi:
        x1, y1, x2, y2 = roi  # even sizes: libx264 needs them
        cmd += ["-vf", f"crop=trunc(iw*{x2 - x1:.4f}/2)*2:trunc(ih*{y2 - y1:.4f}/2)*2:trunc(iw*{x1:.4f}):trunc(ih*{y1:.4f})"]
    subprocess.run(cmd + ["-c:v", "libx264", "-preset", "ultrafast", "-an", str(out)], check=True)
    return out


def _check(res: httpx.Response) -> dict:
    if res.status_code in (401, 403):
        raise MLServerError("ML-сервер отклонил токен: проверьте ML_TOKEN в .env")
    if res.status_code != 200:
        raise MLServerError(f"Ошибка ML-сервера ({res.status_code})")
    return res.json()


def run_ml(video_path: Path, on_progress: Progress) -> dict:
    """Upload, then poll the ML job until done. Returns {verdict, violations}."""
    cfg = CONFIG["ml_server"]
    deadline = time.monotonic() + cfg["timeout_sec"]  # stay under the jury's 5-minute limit
    try:
        with open(video_path, "rb") as f:
            res = httpx.post(f"{ml_url()}/infer_async", headers=_headers(),
                             files={"file": (video_path.name, f, "video/quicktime")},
                             timeout=httpx.Timeout(cfg["upload_timeout_sec"], connect=cfg["connect_timeout_sec"]))
    except httpx.HTTPError:
        raise MLServerError("ML-сервер недоступен: проверьте ML_URL в .env и что туннель запущен")
    ml_job_id = _check(res)["ml_job_id"]

    errors = 0
    while time.monotonic() < deadline:
        time.sleep(cfg["poll_sec"])
        try:
            p = _check(httpx.get(f"{ml_url()}/progress/{ml_job_id}", headers=_headers(),
                                 timeout=cfg["connect_timeout_sec"]))
            errors = 0
        except httpx.HTTPError:  # a short tunnel hiccup shouldn't fail the whole job
            errors += 1
            if errors >= cfg["max_poll_errors"]:
                raise MLServerError("Связь с ML-сервером прервалась во время анализа")
            continue
        if p["status"] == "done":
            return p["result"]
        if p["status"] == "failed":
            raise MLServerError(p.get("error") or "ML-сервер не смог обработать видео")
        if p["status"] == "queued":
            on_progress(0.0, "queued", "В очереди на ML-сервере", p.get("message") or "Сервер обрабатывает другое видео")
        else:
            on_progress(0.95 * (p.get("percent") or 0) / 100, p.get("stage") or "frames",
                        p.get("stage_title"), p.get("message"))
    raise MLServerError(f"ML-сервер не успел за {cfg['timeout_sec']} с")


def analyze_remote(video_path: Path, start_sec: float | None, end_sec: float | None,
                   on_progress: Progress, roi: tuple[float, float, float, float] | None = None,
                   ) -> tuple[list[Violation], list[dict]]:
    offset = start_sec or 0.0
    prepared = prepare_video(video_path, offset, end_sec, roi) if (start_sec or end_sec or roi) else None
    try:
        result = run_ml(prepared or video_path, on_progress)
    finally:
        if prepared:
            prepared.unlink(missing_ok=True)

    on_progress(0.96, "report", "Вердикт и кадры-доказательства")
    types = {t.type.lower(): t for t in load_violation_types().values()}
    norms = load_norms()
    violations, details = [], []
    for v in result.get("violations") or []:
        t = round(float(v["timecode_sec"]) + offset, 2)
        frames = [EvidenceFrame(timestamp_sec=round(float(f["timestamp_sec"]) + offset, 2), image_base64=f["image_base64"])
                  for f in (v.get("evidence_frames") or [])[:CONFIG["postprocess"]["max_evidence_frames"]]]
        if not frames:  # no frames from ML → take the (full, uncropped) frame at the timecode ourselves
            frames = [EvidenceFrame(timestamp_sec=round(f.timestamp_sec, 2),
                                    image_base64=encode_jpeg(f.image, CONFIG["postprocess"]["evidence_jpeg_quality"]))
                      for f in read_frames_at(video_path, [t])]
        violations.append(Violation(type=v["type"], timecode_sec=t, description=v.get("description", ""),
                                    evidence_frames=frames))
        # UI extras: the norm comes ONLY from rules/norms.yaml, matched by the violation type name.
        vtype = types.get(v["type"].strip().lower())
        linked = [norms[n].public() for n in (vtype.norms if vtype else []) if n in norms]
        details.append({
            "confidence": v.get("confidence"),
            # episode bounds if ML sends them; otherwise the UI plays timecode −2 s … +3 s
            "start_sec": round(v["start_sec"] + offset, 2) if v.get("start_sec") is not None else None,
            "end_sec": round(v["end_sec"] + offset, 2) if v.get("end_sec") is not None else None,
            "rule": linked[0] if linked else None,
            "other_norms": linked[1:],
        })
    return violations, details
