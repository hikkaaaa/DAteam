"""Settings: every threshold comes from config.yaml (no hardcoding), the API key from env."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

with open(os.environ.get("CONFIG_PATH", ROOT / "config.yaml"), encoding="utf-8") as f:
    CONFIG: dict = yaml.safe_load(f)


def path(relative: str) -> Path:
    """Resolve a path from config.yaml against the repo root."""
    return ROOT / relative


def api_key() -> str:
    key = os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError("API_KEY is not set. Copy .env.example to .env and set a key.")
    return key
