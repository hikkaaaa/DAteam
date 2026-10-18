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

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.staticfiles import StaticFiles

import feedback
from config import CONFIG, ROOT, api_key, path
from pipeline import remote
from pipeline.remote import MLServerError
from pipeline.runner import analyze_video
from rules import load_documents, load_norms, load_norms_file, load_violation_types
from schemas import JobResponse

logger = logging.getLogger(__name__)

# ---------- Background jobs ----------
# In-memory store: fine for one process. For several workers, move to Redis.
jobs: dict[str, JobResponse] = {}
# UI-only extras per job (not in the ТЗ response): progress, current stage, per-violation details.
extras: dict[str, dict] = {}
jobs_lock = threading.Lock()
executor: ThreadPoolExecutor | None = None


def run_job(job_id: str, video_path: Path, start_sec: float | None, end_sec: float | None,
            roi: tuple[float, float, float, float] | None = None) -> None:
    started = time.perf_counter()
    try:
        violations, details = analyze_video(
            video_path, start_sec, end_sec, roi=roi,
            on_progress=lambda p, stage, title=None, message=None:
                extras[job_id].update(progress=p, stage=stage, title=title, message=message))
        extras[job_id]["details"] = details
        elapsed = time.perf_counter() - started
        if elapsed > CONFIG["jobs"]["target_processing_sec"]:
            logger.warning("Job %s took %.1fs, over the time budget", job_id, elapsed)
        result = JobResponse(job_id=job_id, status="done",
                             verdict="violation" if violations else "no_violation",
                             violations=violations)
    except Exception as e:
        logger.exception("Job %s failed", job_id)
        result = JobResponse(job_id=job_id, status="failed")
        extras[job_id]["error"] = str(e) if isinstance(e, MLServerError) else "Не удалось обработать видео"
    finally:
        if CONFIG["storage"]["delete_video_after_processing"]:
            video_path.unlink(missing_ok=True)
    with jobs_lock:
        jobs[job_id] = result
        extras[job_id].update(progress=1.0, stage="done", title=None, message=None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global executor
    api_key()  # fail fast if the key is missing
    # TODO(ml): load models once here (not per video) so the first job isn't slow.
    executor = ThreadPoolExecutor(max_workers=CONFIG["jobs"]["max_concurrent_jobs"])
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Cash Discipline Video Analyzer", lifespan=lifespan)


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """UI files: the browser re-checks them on every load (cheap 304), so an update never mixes old and new JS."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------- Routes ----------

def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not x_api_key or not secrets.compare_digest(x_api_key, api_key()):
        raise HTTPException(401, "Invalid API key")


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/analyze", dependencies=[Depends(require_api_key)])
def analyze(file: UploadFile = File(...),
            start_sec: float | None = Form(None), end_sec: float | None = Form(None),
            roi: str | None = Form(None)) -> dict:
    """Optional fields (not in the ТЗ; the jury's request works without them):
      start_sec / end_sec — analyze only that segment of the video;
      roi — "x1,y1,x2,y2" in 0..1 of the frame: analyze only the cash desk zone drawn in the UI."""
    if (start_sec is not None and start_sec < 0) or \
            (start_sec is not None and end_sec is not None and end_sec <= start_sec):
        raise HTTPException(400, "Invalid segment: need 0 <= start_sec < end_sec")
    zone = parse_roi(roi) if roi else None
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
        extras[job_id] = {"progress": 1.0, "stage": "upload", "details": []}  # received, analysis not started yet
    executor.submit(run_job, job_id, video_path, start_sec, end_sec, zone)
    return {"job_id": job_id}


def parse_roi(roi: str) -> tuple[float, float, float, float]:
    try:
        x1, y1, x2, y2 = (float(v) for v in roi.split(","))
    except ValueError:
        raise HTTPException(400, "Invalid roi: need x1,y1,x2,y2")
    min_side = CONFIG["roi"]["min_side"]
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1) or x2 - x1 < min_side or y2 - y1 < min_side:
        raise HTTPException(400, f"Invalid roi: need 0 <= x1 < x2 <= 1, 0 <= y1 < y2 <= 1, sides >= {min_side}")
    return x1, y1, x2, y2


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
    """{"progress": 0..1, "stage": ..., "title"?: stage text, "message"?: e.g. "Анализ кадров: 8/12", "error"?}
    Stages: ML server — queued | frames | roles | checklist | detector | lora | rules | report | done;
            local pipeline — detect | rules | report | done."""
    e = job_extras(job_id)
    optional = {k: e.get(k) for k in ("title", "message", "error") if e.get(k)}
    return {"progress": round(e["progress"], 3), "stage": e["stage"], **optional}


@app.post("/api/v1/feedback", dependencies=[Depends(require_api_key)])
def post_feedback(fb: feedback.Feedback) -> dict:
    """Reviewer's verdict on a finding (correct / incorrect) or a missed violation, with a comment."""
    record = feedback.save(fb)
    return {"id": record["id"], "saved": True}


@app.get("/api/v1/feedback", dependencies=[Depends(require_api_key)])
def get_feedback() -> dict:
    """All current feedback + counts per violation type, for the "Обратная связь" page."""
    return feedback.summary()


@app.get("/api/v1/feedback/export", dependencies=[Depends(require_api_key)])
def export_feedback() -> Response:
    """Zip dataset for the ML team: feedback.jsonl, train.jsonl (trainable only), frames/, README.txt."""
    return Response(feedback.export_zip(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="feedback-dataset.zip"'})


@app.get("/api/v1/ml/health", dependencies=[Depends(require_api_key)])
def ml_health() -> dict:
    """Is the ML server reachable: {"status": "ok" | "offline" | "off" (ML_URL not set), ...}"""
    return remote.health()


@app.get("/api/v1/jobs/{job_id}/details", dependencies=[Depends(require_api_key)])
def get_details(job_id: str) -> dict:
    """Per-violation extras in the same order as `violations`: confidence, interval, norm + quote."""
    return {"violations": job_extras(job_id)["details"]}


@app.get("/api/v1/norms", dependencies=[Depends(require_api_key)])
def get_norms() -> dict:
    """Normative base for the "Нормативная база РК" and "Фильтры" pages:
    documents, norms (with the violation types that cite them) and the violation types themselves."""
    types = load_violation_types()
    return {
        "updated_at": load_norms_file().get("updated_at"),
        "documents": [{**d.model_dump(), "short": d.short} for d in load_documents().values()],
        "norms": [{**n.public(), "types": [{"id": t.id, "type": t.type} for t in types.values() if n.id in t.norms]}
                  for n in load_norms().values()],
        "violation_types": [{"id": t.id, "type": t.type, "norms": t.norms, "status": t.status} for t in types.values()],
    }


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
