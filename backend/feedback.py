"""Reviewer feedback on the model's answers ("Верно" / "Неверно" / "Модель пропустила").

Stored on the server in storage/feedback/ (config.yaml → storage.feedback_dir):
  feedback.jsonl   append-only log; the last record with a given id wins (re-votes, deletions)
  train.jsonl      current trainable feedback only (rewritten after every verdict)
  frames/<id>.jpg  the evidence frame the reviewer looked at
Exported as a zip dataset for the ML team, who review it and periodically re-train the LoRA adapter.
Only records with trainable=true go to train.jsonl.
"""

import base64
import binascii
import hashlib
import io
import json
import threading
import time
import zipfile
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from config import CONFIG, path

_lock = threading.Lock()


class Feedback(BaseModel):
    id: str = Field(min_length=1, max_length=120)          # stable per finding, so a re-vote replaces it
    kind: Literal["finding", "missed"]                     # a model finding / a violation the model missed
    verdict: Literal["correct", "incorrect", "missed"]
    job_id: str | None = Field(None, max_length=64)
    file_name: str = Field("", max_length=300)
    type: str = Field("", max_length=300)                  # violation type
    timecode_sec: float | None = None
    description: str | None = Field(None, max_length=5000)  # the model's description (findings)
    comment: str = Field("", max_length=3000)
    trainable: bool = False                                # may the ML team use it for training
    frame_base64: str | None = Field(None, max_length=8_000_000)
    deleted: bool = False


def _dir():
    return path(CONFIG["storage"]["feedback_dir"])


def _frame_name(fid: str) -> str:
    return hashlib.sha1(fid.encode()).hexdigest()[:16] + ".jpg"


def save(fb: Feedback) -> dict:
    record = fb.model_dump(exclude={"frame_base64"}) | {"created_at": time.time(), "frame": None}
    if fb.frame_base64 and not fb.deleted:
        try:
            jpeg = base64.b64decode(fb.frame_base64, validate=True)
        except binascii.Error:
            jpeg = b""
        if jpeg[:2] == b"\xff\xd8":  # JPEG only
            record["frame"] = _frame_name(fb.id)
            (_dir() / "frames").mkdir(parents=True, exist_ok=True)
            (_dir() / "frames" / record["frame"]).write_bytes(jpeg)
    with _lock:
        _dir().mkdir(parents=True, exist_ok=True)
        with open(_dir() / "feedback.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _write_train()
    return record


def _train_lines(items: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items if r["trainable"])


def _write_train() -> None:
    """storage/feedback/train.jsonl: only trainable feedback, rewritten after every new verdict."""
    tmp = _dir() / "train.jsonl.tmp"
    tmp.write_text(_train_lines(latest()), encoding="utf-8")
    tmp.replace(_dir() / "train.jsonl")  # atomic: readers never see a half-written file


def latest() -> list[dict]:
    """Current feedback: the last record per id, deleted ones dropped, newest first."""
    log = _dir() / "feedback.jsonl"
    if not log.exists():
        return []
    by_id: dict[str, dict] = {}
    with _lock, open(log, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                by_id[record["id"]] = record
    return sorted((r for r in by_id.values() if not r["deleted"]), key=lambda r: -r["created_at"])


def summary(with_frames: bool = True) -> dict:
    items = latest()
    per_type: dict[str, dict] = defaultdict(lambda: {"correct": 0, "incorrect": 0, "missed": 0})
    for r in items:
        per_type[r["type"] or "—"][r["verdict"]] += 1
    if with_frames:
        for r in items:
            frame = r["frame"] and _dir() / "frames" / r["frame"]
            r["frame_base64"] = base64.b64encode(frame.read_bytes()).decode() if frame and frame.exists() else None
    return {
        "total": len(items),
        "correct": sum(r["verdict"] == "correct" for r in items),
        "incorrect": sum(r["verdict"] == "incorrect" for r in items),
        "missed": sum(r["verdict"] == "missed" for r in items),
        "trainable": sum(r["trainable"] for r in items),
        "per_type": [{"type": t, **c} for t, c in sorted(per_type.items(), key=lambda kv: -sum(kv[1].values()))],
        "items": items,
    }


README = """Датасет отзывов проверяющих (Кассовый контроль)

feedback.jsonl  все актуальные отзывы, по одному JSON на строку
train.jsonl     только отзывы с trainable=true — их можно брать в обучение
frames/         кадры, на которые смотрел проверяющий (поле "frame")

Поля: kind (finding — находка модели, missed — модель пропустила), verdict (correct / incorrect / missed),
type, timecode_sec, description (ответ модели), comment (комментарий проверяющего), file_name, job_id,
trainable (можно ли использовать для обучения).
"""


def export_zip() -> bytes:
    items = latest()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README)
        z.writestr("feedback.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items))
        z.writestr("train.jsonl", _train_lines(items))
        for r in items:
            frame = r["frame"] and _dir() / "frames" / r["frame"]
            if frame and frame.exists():
                z.write(frame, f"frames/{r['frame']}")
    return buf.getvalue()
