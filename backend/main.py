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

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from config import CONFIG, ROOT, api_key, path
from pipeline.runner import analyze_video
from schemas import JobResponse

logger = logging.getLogger(__name__)

# ---------- Background jobs ----------
# In-memory store: fine for one process. For several workers, move to Redis.
jobs: dict[str, JobResponse] = {}
jobs_lock = threading.Lock()
executor: ThreadPoolExecutor | None = None


def run_job(job_id: str, video_path: Path) -> None:
    started = time.perf_counter()
    try:
        violations = analyze_video(video_path)
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
def analyze(file: UploadFile = File(...)) -> dict:
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
    executor.submit(run_job, job_id, video_path)
    return {"job_id": job_id}


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse, response_model_exclude_none=True,
         dependencies=[Depends(require_api_key)])
def get_job(job_id: str) -> JobResponse:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
