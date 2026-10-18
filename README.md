# Кассовый контроль — Datathon 26

Local AI service that finds visible signs of possible cash-discipline violations in cashier videos.
Requirements and constraints: [CLAUDE.md](CLAUDE.md).

> **Models run on the team's GPU server** (Qwen3-VL-8B + LoRA + detector, `ml_server` on port 8001).
> The backend sends each video to `POST {ML_URL}/infer` (header `X-ML-Token`) and returns the answer
> in the ТЗ format. Set `ML_URL` / `ML_TOKEN` in `.env`. Without `ML_URL` the local stub pipeline runs
> (`TODO(ml)` in `pipeline/model.py`) and every video returns `no_violation`.
>
> For the jury, run this backend on the GPU server itself (`ML_URL=http://localhost:8001`) and expose only
> port 8000 through the tunnel, so videos never leave the server on their way to the models.

## Run

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # Python 3.11 — 3.14 is not supported
pip install -r backend/requirements.txt
cp .env.example .env                                     # set API_KEY
cd backend && uvicorn main:app --reload                  # UI: http://localhost:8000  (?demo = sample results)
```

Docker + GPU: `docker compose up --build` · Tests: `pytest tests`

## Layout

```
backend/
  main.py              API (POST /api/v1/analyze, GET /api/v1/jobs/{id}), auth, job queue
                       analyze also takes optional start_sec / end_sec (segment) and roi (cash desk
                       zone drawn in the UI; the video is cropped to it) — not in the ТЗ
                       optional UI endpoints (not in the ТЗ; ТЗ response stays exact):
                       /jobs/{id}/progress, /jobs/{id}/details, /norms, /ml/health
  schemas.py           response format fixed by the ТЗ
  rules.py             loads rules/*.yaml
  pipeline/
    model.py           ← ML models: detector, scene state, VLM verifier   TODO(ml)
    events.py          ← rule detectors per violation type, merging        TODO(data)
    video.py           frame sampling, JPEG evidence
    remote.py          ← ML server client: POST {ML_URL}/infer, segment cut, norms from norms.yaml
    runner.py          video -> violations (ML server if ML_URL is set, else the local steps)
training/
  train_detector.py    ← trains the detector into weights/detector.pt      TODO(ml)
rules/
  norms.yaml           legal references (documents + 30 norms), the ONLY allowed source; `updated_at` = base date
  violation_types.yaml 28 violation types (all «черновик»); only `подтверждён` ones are output
scripts/
  download_weights.py  the only step that uses the network
  evaluate.py          jury metrics -> reports/
frontend/              index.html, style.css
  app.js               main flow: upload, progress, results
  player.js            audit player: frame step, speed, episodes, hotkeys, ROI zones
  norms.js             "Нормативная база РК" tab
  library.js           history search, manual annotations, categories, category feed
  filters.js           "Фильтры": all videos, sorting, filter by laws
  storage.js           IndexedDB: analyzed sessions + videos saved in this browser only
  icons.js             Lucide icons (inline, offline)
  report.js            PDF report (browser print → Save as PDF)
  assets/demo/         sample videos for the demo buttons (git-ignored)
config.yaml            every threshold and parameter
weights/  data/        git-ignored
```

## Enabling a violation type

1. Make sure its norms in `rules/norms.yaml` have verbatim quotes (and set `adilet_checked_at` once checked on adilet).
2. Write the detector in `backend/pipeline/events.py` and register it in `DETECTORS` under the type's `key`.
3. Put its thresholds under `detectors.<key>` in `config.yaml`, then run `scripts/evaluate.py`.
4. Set `status: подтверждён` in `rules/violation_types.yaml`. Only then the type can appear in the API.
