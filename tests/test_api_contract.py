"""API contract test (format fixed by the ТЗ). Run after ANY change to the API:

    pytest tests/test_api_contract.py
"""

import base64
import time
from fractions import Fraction

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

from main import app
from schemas import EvidenceFrame, Violation

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mov_file(tmp_path):
    """A 2-second synthetic .mov, so the real decoding path is exercised."""
    path = tmp_path / "sample.mov"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.time_base = Fraction(1, 10)
        for i in range(20):
            image = np.full((48, 64, 3), i * 10, dtype=np.uint8)
            container.mux(stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
        container.mux(stream.encode())
    return path


def wait_for_job(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS).json()
        if body["status"] != "processing":
            return body
        time.sleep(0.2)
    raise TimeoutError(job_id)


def upload(client, path, headers=HEADERS):
    with open(path, "rb") as f:
        return client.post("/api/v1/analyze", files={"file": (path.name, f, "video/quicktime")}, headers=headers)


def test_requires_api_key(client, mov_file):
    assert upload(client, mov_file, headers={}).status_code == 401
    assert upload(client, mov_file, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/jobs/whatever").status_code == 401


def test_rejects_non_mov(client, tmp_path):
    path = tmp_path / "clip.avi"
    path.write_bytes(b"not a video")
    assert upload(client, path).status_code == 415


def test_segment(client, mov_file, monkeypatch):
    seen = {}
    monkeypatch.setattr("main.analyze_video", lambda path, start, end, **_: seen.update(start=start, end=end) or ([], []))
    with open(mov_file, "rb") as f:
        r = client.post("/api/v1/analyze", files={"file": (mov_file.name, f, "video/quicktime")},
                        data={"start_sec": "0.5", "end_sec": "1.5"}, headers=HEADERS)
    assert wait_for_job(client, r.json()["job_id"])["verdict"] == "no_violation"
    assert seen == {"start": 0.5, "end": 1.5}

    with open(mov_file, "rb") as f:
        bad = client.post("/api/v1/analyze", files={"file": (mov_file.name, f, "video/quicktime")},
                          data={"start_sec": "2", "end_sec": "1"}, headers=HEADERS)
    assert bad.status_code == 400


def test_roi(client, mov_file, monkeypatch):
    """Optional cash desk zone: validated, passed to the pipeline; the jury's request works without it."""
    seen = {}
    monkeypatch.setattr("main.analyze_video", lambda path, start, end, roi=None, **_: seen.update(roi=roi) or ([], []))
    for roi, code in [("0.1,0.2,0.6,0.9", 200), ("0.6,0.2,0.1,0.9", 400), ("0,0,0.01,0.01", 400), ("a,b", 400)]:
        with open(mov_file, "rb") as f:
            r = client.post("/api/v1/analyze", files={"file": (mov_file.name, f, "video/quicktime")},
                            data={"roi": roi}, headers=HEADERS)
        assert r.status_code == code, roi
        if code == 200:
            wait_for_job(client, r.json()["job_id"])
            assert seen["roi"] == (0.1, 0.2, 0.6, 0.9)


def test_reference_endpoints(client):
    assert client.get("/api/v1/norms").status_code == 401
    base = client.get("/api/v1/norms", headers=HEADERS).json()
    assert {"updated_at", "documents", "norms", "violation_types"} <= base.keys()


def test_rejects_too_large(client, mov_file, monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG["server"], "max_upload_mb", 0)
    assert upload(client, mov_file).status_code == 413


def test_unknown_job_is_404(client):
    assert client.get("/api/v1/jobs/does-not-exist", headers=HEADERS).status_code == 404


def test_no_violation_flow(client, mov_file):
    response = upload(client, mov_file)
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert isinstance(job_id, str) and job_id

    body = wait_for_job(client, job_id)
    assert body["job_id"] == job_id
    assert body["status"] == "done"
    assert body["verdict"] == "no_violation"
    assert body["violations"] == []
    assert set(body) == {"job_id", "status", "verdict", "violations"}  # exactly the ТЗ fields
    progress = client.get(f"/api/v1/jobs/{job_id}/progress", headers=HEADERS).json()
    assert progress == {"progress": 1.0, "stage": "done"}
    assert client.get(f"/api/v1/jobs/{job_id}/details", headers=HEADERS).json() == {"violations": []}


def test_violation_shape(client, mov_file, monkeypatch, tmp_path):
    """Pipeline output is faked so the response shape of a 'violation' verdict is checked."""
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
    jpeg_b64 = base64.b64encode(buffer.getvalue()).decode()

    fake = [Violation(
        type="Тестовый тип", timecode_sec=1.5, description="Тест",
        evidence_frames=[EvidenceFrame(timestamp_sec=1.5, image_base64=jpeg_b64)],
    )]
    monkeypatch.setattr("main.analyze_video", lambda *_, **__: (fake, [{"confidence": 0.9}]))

    body = wait_for_job(client, upload(client, mov_file).json()["job_id"])
    assert body["status"] == "done"
    assert body["verdict"] == "violation"
    assert len(body["violations"]) == 1
    assert set(body) == {"job_id", "status", "verdict", "violations"}  # extras never leak into the ТЗ response
    assert set(body["violations"][0]) == {"type", "timecode_sec", "description", "evidence_frames"}
    details = client.get(f"/api/v1/jobs/{body['job_id']}/details", headers=HEADERS).json()
    assert details == {"violations": [{"confidence": 0.9}]}

    violation = body["violations"][0]
    assert {"type", "timecode_sec", "description", "evidence_frames"} <= violation.keys()
    assert isinstance(violation["timecode_sec"], float)
    assert 1 <= len(violation["evidence_frames"]) <= 3
    for frame in violation["evidence_frames"]:
        assert isinstance(frame["timestamp_sec"], float)
        assert not frame["image_base64"].startswith("data:")
        assert base64.b64decode(frame["image_base64"])[:2] == b"\xff\xd8"  # JPEG magic


def test_failed_job(client, tmp_path):
    path = tmp_path / "broken.mov"
    path.write_bytes(b"definitely not a video")
    body = wait_for_job(client, upload(client, path).json()["job_id"])
    assert body["status"] == "failed"


def test_ml_server_response(client, mov_file, monkeypatch):
    """ML server answer (ТЗ format) -> our API: segment offset, frames filled in, norm from norms.yaml."""
    from pipeline import remote
    from rules import load_violation_types

    v04 = load_violation_types()["V04"]
    monkeypatch.setenv("ML_URL", "http://ml.test")
    sent = {}

    def fake_ml(path, on_progress):  # what the ML server would receive: the segment, cropped to the zone
        with av.open(str(path)) as c:
            s = c.streams.video[0]
            sent.update(size=(s.width, s.height), duration=float(c.duration / av.time_base))
        return {"verdict": "violation", "violations": [
            {"type": v04.type, "timecode_sec": 0.5, "description": "Тест", "evidence_frames": [], "confidence": 0.8}]}
    monkeypatch.setattr(remote, "run_ml", fake_ml)
    with open(mov_file, "rb") as f:
        r = client.post("/api/v1/analyze", files={"file": (mov_file.name, f, "video/quicktime")},
                        data={"start_sec": "1.0", "end_sec": "2.0", "roi": "0,0,0.5,0.5"}, headers=HEADERS)
    body = wait_for_job(client, r.json()["job_id"])
    assert sent["size"] == (32, 24) and 0.8 <= sent["duration"] <= 1.2
    assert set(body) == {"job_id", "status", "verdict", "violations"}
    v = body["violations"][0]
    assert v["timecode_sec"] == 1.5 and len(v["evidence_frames"]) == 1
    details = client.get(f"/api/v1/jobs/{body['job_id']}/details", headers=HEADERS).json()["violations"][0]
    assert details["confidence"] == 0.8 and details["rule"]["id"] == v04.norms[0]

    def down(path, on_progress):
        raise remote.MLServerError("ML-сервер недоступен")
    monkeypatch.setattr(remote, "run_ml", down)
    body = wait_for_job(client, upload(client, mov_file).json()["job_id"])
    assert body == {"job_id": body["job_id"], "status": "failed"}
    assert client.get(f"/api/v1/jobs/{body['job_id']}/progress", headers=HEADERS).json()["error"] == "ML-сервер недоступен"


def test_ml_server_polling(tmp_path, monkeypatch):
    """POST /infer_async, then GET /progress until done: stages reach the UI, the result comes back."""
    import httpx
    from config import CONFIG
    from pipeline import remote

    monkeypatch.setenv("ML_URL", "http://ml.test")
    monkeypatch.setitem(CONFIG["ml_server"], "poll_sec", 0)
    answers = iter([
        {"status": "queued"},
        {"status": "processing", "stage": "checklist", "stage_title": "Анализ кадров (Qwen)", "percent": 44,
         "message": "Анализ кадров: 8/12"},
        {"status": "done", "result": {"verdict": "no_violation", "violations": []}},
    ])
    request = httpx.Request("GET", "http://ml.test")
    monkeypatch.setattr(remote.httpx, "post", lambda *a, **k: httpx.Response(200, json={"ml_job_id": "j1"}, request=request))
    monkeypatch.setattr(remote.httpx, "get", lambda *a, **k: httpx.Response(200, json=next(answers), request=request))
    video = tmp_path / "v.mov"
    video.write_bytes(b"x")
    seen = []
    result = remote.run_ml(video, lambda *args: seen.append(args))
    assert result == {"verdict": "no_violation", "violations": []}
    assert [s[1] for s in seen] == ["queued", "checklist"]
    assert seen[1][2:] == ("Анализ кадров (Qwen)", "Анализ кадров: 8/12") and round(seen[1][0], 3) == 0.418


def test_rules_files_are_consistent():
    """rules/*.yaml parse, every type's norm exists, and only confirmed types can reach the API."""
    from rules import CONFIRMED, load_active_rules, load_norms, load_violation_types

    norms, types = load_norms(), load_violation_types()
    assert norms and types
    for t in types.values():
        assert t.norms and all(n in norms for n in t.norms), t.id
        assert t.template.strip(), t.id
    active, _ = load_active_rules()
    assert all(t.status == CONFIRMED for t in active.values())


def test_description_uses_template_and_norm():
    from pipeline.events import Event, describe
    from rules import load_norms, load_violation_types

    norms, types = load_norms(), load_violation_types()
    v04 = types["V04"]
    text = describe(Event("V04", 65, 103, 80, 0.9), v04, norms[v04.norms[0]])
    assert text.startswith("С 1:05 на столе кассира видны наличные")
    assert "≈38 с" in text and "Норма: п. 20 ч. 2" in text and "Правил № 231" in text
