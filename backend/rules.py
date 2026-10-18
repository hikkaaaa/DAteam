"""Loads rules/norms.yaml and rules/violation_types.yaml (formats are described in the files' headers).

A violation type can appear in the API output only if:
  - it has `status: подтверждён`, and
  - its main norm (`norms[0]`) exists in norms.yaml with a non-empty verbatim `quote`, and
  - a detector named `key` is registered in backend/pipeline/events.py.
Incomplete norms are skipped, so an unverified legal reference can never reach the output.
"""

import logging
import re

import yaml
from pydantic import BaseModel, ValidationError, constr

from config import CONFIG, path

logger = logging.getLogger(__name__)
Text = constr(strip_whitespace=True, min_length=1)
CONFIRMED = "подтверждён"


class Document(BaseModel):
    key: str
    title: Text
    approved_by: Text
    edition: str | None = None
    url: Text

    @property
    def short(self) -> str:
        """"Правила № 231" from "Постановление … № 231 (рег. МЮ …)"."""
        number = re.search(r"№\s*(\d+)", self.approved_by)
        return f"Правила № {number.group(1)}" if number else self.title[:40]


class Norm(BaseModel):
    id: str
    doc: str
    clause: Text
    quote: Text
    note: str | None = None
    source_checked: str | None = None
    adilet_checked_at: str | None = None  # null = not yet verified by hand on adilet.zan.kz
    document: Document

    @property
    def source(self) -> str:
        """Reference used in descriptions, e.g. "п. 22 (гл. 2, параграф 2) Правил № 231 (Постановление …)"."""
        short = self.document.short.replace("Правила", "Правил", 1)
        return f"{self.clause} {short} ({self.document.approved_by})"

    def public(self) -> dict:
        """Everything the UI shows for a norm (Нормативная база РК, result cards, PDF)."""
        return {**self.model_dump(exclude={"document"}), "document": self.document.title,
                "document_short": self.document.short, "approved_by": self.document.approved_by,
                "edition": self.document.edition, "url": self.document.url}


class ViolationType(BaseModel):
    id: str
    key: str | None = None            # detector name in pipeline/events.py (null = not written yet)
    type: str                         # short name, goes to the API as violations[].type
    criterion: str
    norms: list[str]                  # first one is the main norm used in the description
    not_verifiable: str | None = None
    confidence: str | None = None     # high | medium | low
    status: str = "черновик"          # черновик | подтверждён
    template: str                     # placeholders: {t} start, {dur} duration, {n} people
    note: str | None = None


def _read(relative: str):
    with open(path(relative), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_norms_file() -> dict:
    return _read(CONFIG["rules"]["norms_path"])


def load_documents() -> dict[str, Document]:
    docs = {}
    for key, raw in (load_norms_file().get("documents") or {}).items():
        try:
            docs[key] = Document.model_validate({"key": key, **raw})
        except ValidationError as exc:
            logger.warning("Skipping incomplete document %s: %s", key, exc)
    return docs


def load_norms() -> dict[str, Norm]:
    docs = load_documents()
    norms = {}
    for entry in load_norms_file().get("norms") or []:
        doc = docs.get(entry.get("doc"))
        if doc is None:
            logger.warning("Skipping norm %s: unknown document %s", entry.get("id", "?"), entry.get("doc"))
            continue
        try:
            norm = Norm.model_validate({**entry, "document": doc})
            norms[norm.id] = norm
        except ValidationError as exc:
            logger.warning("Skipping incomplete norm %s: %s", entry.get("id", "?"), exc)
    return norms


def load_violation_types() -> dict[str, ViolationType]:
    raw = _read(CONFIG["rules"]["violation_types_path"])
    return {t.id: t for t in (ViolationType.model_validate(e) for e in raw.get("types") or [])}


def load_active_rules() -> tuple[dict[str, ViolationType], dict[str, Norm]]:
    """Types allowed in the output, plus all norms (detector availability is checked in events.py)."""
    norms = load_norms()
    active = {
        type_id: t for type_id, t in load_violation_types().items()
        if t.status == CONFIRMED and t.norms and t.norms[0] in norms
    }
    return active, norms
