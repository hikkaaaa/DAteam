"""FastAPI service: API under /api/v1 (format fixed by the ТЗ), web UI under /.

Run:  cd backend && uvicorn main:app --reload
"""

import os

# Runtime must stay offline: weights are pre-downloaded by scripts/download_weights.py.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import logging
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from config import CONFIG, ROOT, api_key, path
from pipeline.runner import analyze_video
from rules import load_norms
from schemas import JobResponse

logger = logging.getLogger(__name__)

# ---------- Background jobs ----------
# In-memory store: fine for one process. For several workers, move to Redis.
jobs: dict[str, JobResponse] = {}
# UI-only extras per job (not in the ТЗ response): progress, current stage, per-violation details.
extras: dict[str, dict] = {}
jobs_lock = threading.Lock()
executor: ThreadPoolExecutor | None = None


def run_job(job_id: str, video_path: Path, start_sec: float | None, end_sec: float | None) -> None:
    started = time.perf_counter()
    try:
        violations, details = analyze_video(
            video_path, start_sec, end_sec,
            on_progress=lambda p, stage: extras[job_id].update(progress=p, stage=stage))
        extras[job_id]["details"] = details
        elapsed = time.perf_counter() - started
        if elapsed > CONFIG["jobs"]["target_processing_sec"]:
            logger.warning("Job %s took %.1fs, over the time budget", job_id, elapsed)
        result = JobResponse(job_id=job_id, status="done",
                             verdict="violation" if violations else "no_violation",
                             violations=violations)
    except Exception:
        logger.exception("Job %s failed", job_id)
        result = JobResponse(job_id=job_id, status="failed")
    finally:
        if CONFIG["storage"]["delete_video_after_processing"]:
            video_path.unlink(missing_ok=True)
    with jobs_lock:
        jobs[job_id] = result
        extras[job_id].update(progress=1.0, stage="done")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global executor
    api_key()  # fail fast if the key is missing
    # TODO(ml): load models once here (not per video) so the first job isn't slow.
    executor = ThreadPoolExecutor(max_workers=CONFIG["jobs"]["max_concurrent_jobs"])
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Cash Discipline Video Analyzer", lifespan=lifespan)


# ---------- Routes ----------

def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not x_api_key or not secrets.compare_digest(x_api_key, api_key()):
        raise HTTPException(401, "Invalid API key")


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/analyze", dependencies=[Depends(require_api_key)])
def analyze(file: UploadFile = File(...),
            start_sec: float | None = Form(None), end_sec: float | None = Form(None)) -> dict:
    """`start_sec` / `end_sec` are optional (not in the ТЗ): analyze only that segment of the video."""
    if (start_sec is not None and start_sec < 0) or \
            (start_sec is not None and end_sec is not None and end_sec <= start_sec):
        raise HTTPException(400, "Invalid segment: need 0 <= start_sec < end_sec")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in CONFIG["server"]["allowed_extensions"]:
        raise HTTPException(415, f"Unsupported file type '{extension}'")

    job_id = uuid.uuid4().hex[:12]
    video_path = path(CONFIG["storage"]["uploads_dir"]) / f"{job_id}{extension}"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    with open(video_path, "wb") as out:
        shutil.copyfileobj(file.file, out)
    if video_path.stat().st_size > CONFIG["server"]["max_upload_mb"] * 1024 * 1024:
        video_path.unlink()
        raise HTTPException(413, f"File is larger than {CONFIG['server']['max_upload_mb']} MB")

    with jobs_lock:
        jobs[job_id] = JobResponse(job_id=job_id, status="processing")
        extras[job_id] = {"progress": 0.0, "stage": "detect", "details": []}
    executor.submit(run_job, job_id, video_path, start_sec, end_sec)
    return {"job_id": job_id}


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse, response_model_exclude_none=True,
         dependencies=[Depends(require_api_key)])
def get_job(job_id: str) -> JobResponse:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


# ---------- Optional endpoints for the UI (not in the ТЗ; the ТЗ endpoints above are unchanged) ----------

def job_extras(job_id: str) -> dict:
    with jobs_lock:
        if job_id not in extras:
            raise HTTPException(404, "Job not found")
        return extras[job_id]


@app.get("/api/v1/jobs/{job_id}/progress", dependencies=[Depends(require_api_key)])
def get_progress(job_id: str) -> dict:
    """{"progress": 0..1, "stage": detect | rules | report | done}"""
    e = job_extras(job_id)
    return {"progress": round(e["progress"], 3), "stage": e["stage"]}


@app.get("/api/v1/jobs/{job_id}/details", dependencies=[Depends(require_api_key)])
def get_details(job_id: str) -> dict:
    """Per-violation extras in the same order as `violations`: confidence, interval, norm + quote."""
    return {"violations": job_extras(job_id)["details"]}


@app.get("/api/v1/norms", dependencies=[Depends(require_api_key)])
def get_norms() -> list[dict]:
    """Normative base from rules/norms.yaml (complete entries only) for the "Нормативная база РК" tab."""
    return [norm.model_dump() for norm in load_norms().values()]


@app.get("/api/v1/zones", dependencies=[Depends(require_api_key)])
def get_zones() -> dict:
    """Scene zones from config.yaml (normalized x1, y1, x2, y2; null = not marked yet) for the ROI overlay."""
    return {name: box for name, box in CONFIG["scene"]["zones"].items() if box}


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
