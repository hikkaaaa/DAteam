import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("API_KEY", "test-key")
os.environ["ML_URL"] = ""  # tests never call the real ML server (beats ML_URL from .env)


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path, monkeypatch):
    """Keep uploaded test videos out of the repo."""
    from config import CONFIG

    monkeypatch.setitem(CONFIG["storage"], "uploads_dir", str(tmp_path / "uploads"))
    monkeypatch.setitem(CONFIG["storage"], "feedback_dir", str(tmp_path / "feedback"))
