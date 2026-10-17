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
    monkeypatch.setattr("main.analyze_video", lambda path, start, end, **_: seen.update(start=start, end=end) or [])
    with open(mov_file, "rb") as f:
        r = client.post("/api/v1/analyze", files={"file": (mov_file.name, f, "video/quicktime")},
                        data={"start_sec": "0.5", "end_sec": "1.5"}, headers=HEADERS)
    assert wait_for_job(client, r.json()["job_id"])["verdict"] == "no_violation"
    assert seen == {"start": 0.5, "end": 1.5}

    with open(mov_file, "rb") as f:
        bad = client.post("/api/v1/analyze", files={"file": (mov_file.name, f, "video/quicktime")},
                          data={"start_sec": "2", "end_sec": "1"}, headers=HEADERS)
    assert bad.status_code == 400


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
    assert progress == {"progress": 1.0}


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
    monkeypatch.setattr("main.analyze_video", lambda *_, **__: fake)

    body = wait_for_job(client, upload(client, mov_file).json()["job_id"])
    assert body["status"] == "done"
    assert body["verdict"] == "violation"
    assert len(body["violations"]) == 1

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
