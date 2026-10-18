"""
ml_server.py — HTTP-обёртка над analyze() для бэкенда.

Запуск на GPU-сервере:
    cd /workspace/datathon
    export ML_TOKEN=$(cat ml_token.txt)
    HF_HUB_OFFLINE=1 nohup uvicorn ml_server:app --app-dir src --host 0.0.0.0 --port 8001 > ml_server.log 2>&1 &

Для бэкенда (везде заголовок X-ML-Token):
    GET  /health                      -> {"status": "ok", "pipeline": ..., "detector": ..., "busy": ..., "queue": N}

    Синхронно (как раньше):
    POST /infer          (file)       -> {"verdict", "violations", "processing_sec"}   — ждёт до конца

    С прогрессом (для отображения этапов в интерфейсе):
    POST /infer_async    (file)       -> {"ml_job_id": "..."}                           — сразу
    GET  /progress/{ml_job_id}        -> {"status": "queued|processing|done|failed",
                                          "stage": "frames|roles|checklist|detector|lora|rules|done",
                                          "stage_title": "Нарезка видео на кадры", "percent": 0..100,
                                          "message": "...", "elapsed_sec": ...,
                                          "result": {...} (когда done), "error": "..." (когда failed)}
    Видео обрабатываются строго по одному (одна видеокарта); остальные ждут в очереди (status=queued).
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile

sys.path.insert(0, str(Path(__file__).parent))
import detector  # noqa: E402
import ml_config as cfg  # noqa: E402
from model import analyze, get_model  # noqa: E402

TOKEN = os.environ.get("ML_TOKEN", "")
_lock = threading.Lock()          # одна видеокарта — одно видео за раз
_jobs = {}                        # ml_job_id -> состояние
_jobs_lock = threading.Lock()
MAX_JOBS_KEPT = 200

STAGE_TITLES = {
    "queued": "В очереди",
    "frames": "Нарезка видео на кадры",
    "roles": "Определение ролей людей",
    "checklist": "Анализ кадров (Qwen)",
    "detector": "Детектор предметов",
    "lora": "Проверка дообученной моделью (LoRA)",
    "rules": "Правила и формирование результата",
    "done": "Готово",
    "failed": "Ошибка",
}


@asynccontextmanager
async def lifespan(_app):
    t0 = time.time()
    get_model()  # модель грузится один раз при старте, а не на первом видео жюри
    print(f"[ml_server] модель загружена за {time.time() - t0:.0f} с, режим {cfg.PIPELINE}", flush=True)
    yield


app = FastAPI(title="ForteBank cash discipline ML", lifespan=lifespan)


def _check_token(token):
    if TOKEN and token != TOKEN:
        raise HTTPException(status_code=401, detail="invalid X-ML-Token")


def _save_upload(file):
    suffix = Path(file.filename or "video.mov").suffix or ".mov"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        return tmp.name


def _update(job_id, **kw):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def _run_job(job_id, path, name):
    try:
        with _lock:
            t0 = time.time()
            _update(job_id, status="processing", started=t0)

            def progress(stage, percent, message):
                _update(job_id, stage=stage, percent=percent, message=message)

            result = analyze(path, progress=progress)
            result["processing_sec"] = round(time.time() - t0, 1)
        _update(job_id, status="done", stage="done", percent=100, message="Готово", result=result,
                finished=time.time())
        print(f"[ml_server] {name}: {result['verdict']}, {len(result['violations'])} наруш., "
              f"{result['processing_sec']} с", flush=True)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _update(job_id, status="failed", stage="failed", message=str(e), error=str(e), finished=time.time())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@app.get("/health")
def health():
    with _jobs_lock:
        queued = sum(1 for j in _jobs.values() if j["status"] == "queued")
    return {"status": "ok", "pipeline": cfg.PIPELINE, "detector": detector.available(),
            "busy": _lock.locked(), "queue": queued}


@app.post("/infer")
def infer(file: UploadFile = File(...), x_ml_token: str = Header(default="")):
    _check_token(x_ml_token)
    path = _save_upload(file)
    try:
        t0 = time.time()
        with _lock:
            result = analyze(path)
        result["processing_sec"] = round(time.time() - t0, 1)
        print(f"[ml_server] {file.filename}: {result['verdict']}, "
              f"{len(result['violations'])} наруш., {result['processing_sec']} с", flush=True)
        return result
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"analyze failed: {e}") from e
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@app.post("/infer_async")
def infer_async(file: UploadFile = File(...), x_ml_token: str = Header(default="")):
    _check_token(x_ml_token)
    path = _save_upload(file)
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        if len(_jobs) >= MAX_JOBS_KEPT:  # убрать самые старые завершённые
            done = sorted((j for j in _jobs.items() if j[1]["status"] in ("done", "failed")),
                          key=lambda kv: kv[1]["created"])
            for k, _ in done[:len(_jobs) - MAX_JOBS_KEPT + 1]:
                _jobs.pop(k, None)
        _jobs[job_id] = {"status": "queued", "stage": "queued", "percent": 0, "message": "В очереди",
                         "created": time.time(), "result": None, "error": None}
    threading.Thread(target=_run_job, args=(job_id, path, file.filename), daemon=True).start()
    return {"ml_job_id": job_id}


@app.get("/progress/{job_id}")
def progress(job_id: str, x_ml_token: str = Header(default="")):
    _check_token(x_ml_token)
    with _jobs_lock:
        job = dict(_jobs.get(job_id) or {})
    if not job:
        raise HTTPException(status_code=404, detail="unknown ml_job_id")
    start = job.get("started") or job["created"]
    end = job.get("finished") or time.time()
    return {"ml_job_id": job_id, "status": job["status"], "stage": job["stage"],
            "stage_title": STAGE_TITLES.get(job["stage"], job["stage"]), "percent": job["percent"],
            "message": job["message"], "elapsed_sec": round(end - start, 1),
            "result": job["result"] if job["status"] == "done" else None, "error": job["error"]}
