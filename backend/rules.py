"""Loads rules/norms.yaml and rules/violation_types.yaml.

A violation type can appear in the API output only if:
  - it has `status: confirmed`, and
  - its `rule_id` points to a complete norm (every field filled, verbatim quote) whose
    `applies_to` lists the type.
Incomplete norms are skipped, so an unverified legal reference can never reach the output.
"""

import logging
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, constr

from config import CONFIG, path

logger = logging.getLogger(__name__)
Text = constr(strip_whitespace=True, min_length=1)


class Norm(BaseModel):
    id: str
    document: Text
    number_date: Text
    clause: Text
    quote: Text
    url: Text
    checked_at: Text
    applies_to: list[str]
    interpretation: Text

    @property
    def source(self) -> str:
        return f"{self.clause} {self.document}, {self.number_date}"


class ViolationType(BaseModel):
    id: str
    type: str
    visual_criterion: str
    rule_id: str | None = None
    status: Literal["draft", "confirmed"] = "draft"
    description_template: str


def _read(relative: str) -> list:
    with open(path(relative), encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def load_norms() -> dict[str, Norm]:
    norms = {}
    for entry in _read(CONFIG["rules"]["norms_path"]):
        try:
            norm = Norm.model_validate(entry)
            norms[norm.id] = norm
        except ValidationError as exc:
            logger.warning("Skipping incomplete norm %s: %s", entry.get("id", "?"), exc)
    return norms


def load_violation_types() -> dict[str, ViolationType]:
    types = (ViolationType.model_validate(e) for e in _read(CONFIG["rules"]["violation_types_path"]))
    return {t.id: t for t in types}


def load_active_rules() -> tuple[dict[str, ViolationType], dict[str, Norm]]:
    """Types allowed in the output, plus the norms they reference."""
    norms = load_norms()
    active = {
        type_id: t for type_id, t in load_violation_types().items()
        if t.status == "confirmed" and t.rule_id in norms and type_id in norms[t.rule_id].applies_to
    }
    return active, norms
