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
  norms.yaml           ← legal references, the ONLY allowed source          TODO(data)
  violation_types.yaml ← violation types; only `confirmed` ones are output
scripts/
  download_weights.py  the only step that uses the network
  evaluate.py          jury metrics -> reports/
frontend/              index.html, style.css
  app.js               main flow: upload, progress, results, history
  player.js            audit player: frame step, speed, episodes, hotkeys, ROI zones
  norms.js             "Нормативная база РК" tab
  report.js            PDF report (browser print → Save as PDF)
  assets/demo/         sample videos for the demo buttons (git-ignored)
config.yaml            every threshold and parameter
weights/  data/        git-ignored
```

## Adding a violation type

1. Add the verified clause to `rules/norms.yaml` (verbatim quote, `applies_to: [Vxx]`).
2. Add the type to `rules/violation_types.yaml` with `rule_id` and `status: confirmed`.
3. Write its detector in `backend/pipeline/events.py` and register it in `DETECTORS`.
4. Put its thresholds under `detectors.Vxx` in `config.yaml`, then run `scripts/evaluate.py`.
