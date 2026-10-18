# Кассовый контроль — Datathon 26

Local AI service that finds visible signs of possible cash-discipline violations in cashier videos.
Requirements and constraints: [CLAUDE.md](CLAUDE.md).

> **Status: skeleton.** The API, job queue, video decoding and UI work end-to-end; the ML models are
> stubs, so every video returns `no_violation`. Search for `TODO(ml)` / `TODO(data)` to find every slot.

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
                       analyze also takes optional start_sec / end_sec (segment; not in the ТЗ)
                       optional UI endpoints (not in the ТЗ; ТЗ response stays exact):
                       /jobs/{id}/progress, /jobs/{id}/details, /norms, /zones
  schemas.py           response format fixed by the ТЗ
  rules.py             loads rules/*.yaml
  pipeline/
    model.py           ← ML models: detector, scene state, VLM verifier   TODO(ml)
    events.py          ← rule detectors per violation type, merging        TODO(data)
    video.py           frame sampling, JPEG evidence
    runner.py          video -> violations (wires the steps together)
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
