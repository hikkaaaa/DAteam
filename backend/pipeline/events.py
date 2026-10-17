"""Scene timeline -> violation events: rule detectors, merging, descriptions.

To add a violation type: write a detector below, register it in DETECTORS,
and put its thresholds under `detectors.<id>` in config.yaml.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from config import CONFIG
from pipeline.model import SceneState
from rules import Norm, ViolationType


@dataclass
class Event:
    type_id: str          # V01, V02, ... from rules/violation_types.yaml
    start_sec: float
    end_sec: float
    peak_sec: float       # moment of highest confidence -> timecode_sec in the API
    confidence: float
    facts: dict = field(default_factory=dict)  # observed facts for the description template


def find_intervals(states: list[SceneState], condition: Callable[[SceneState], bool | None],
                   min_duration_sec: float) -> list[tuple[float, float]]:
    """[start, end] intervals where `condition` is True for at least `min_duration_sec`.
    An unknown (None) value breaks the interval, so we never claim what we didn't see."""
    intervals, start, last = [], None, None
    for s in states + [None]:
        if s is not None and condition(s) is True:
            start = s.timestamp_sec if start is None else start
            last = s.timestamp_sec
            continue
        if start is not None and last - start >= min_duration_sec:
            intervals.append((start, last))
        start = last = None
    return intervals


# ---------- Rule detectors (one per violation type) ----------

def detect_unattended_cash(states: list[SceneState], params: dict) -> list[Event]:
    """V01 (draft): cash stays on the desk while the cashier is away."""
    def condition(s: SceneState) -> bool | None:
        if s.cash_on_desk is None or s.cashier_at_desk is None:
            return None
        return s.cash_on_desk and not s.cashier_at_desk

    # TODO(ml): replace the fixed confidence with a real score (e.g. mean detector confidence).
    return [Event("V01", a, b, (a + b) / 2, confidence=0.7)
            for a, b in find_intervals(states, condition, params["min_duration_sec"])]


# TODO(data): add a detector for every type confirmed in rules/violation_types.yaml.
DETECTORS: dict[str, Callable[[list[SceneState], dict], list[Event]]] = {
    "V01": detect_unattended_cash,
}


def find_events(states: list[SceneState], type_ids: set[str]) -> list[Event]:
    events = []
    for type_id in type_ids & DETECTORS.keys():
        events += DETECTORS[type_id](states, CONFIG["detectors"].get(type_id, {}))
    return events


# ---------- Post-processing ----------

def merge_events(events: list[Event]) -> list[Event]:
    """Merge same-type events closer than merge_gap_sec; drop low confidence (precision first)."""
    cfg = CONFIG["postprocess"]
    merged: list[Event] = []
    for e in sorted(events, key=lambda e: (e.type_id, e.start_sec)):
        last = merged[-1] if merged else None
        if last and last.type_id == e.type_id and e.start_sec - last.end_sec <= cfg["merge_gap_sec"]:
            best = max(last, e, key=lambda x: x.confidence)
            merged[-1] = Event(e.type_id, last.start_sec, max(last.end_sec, e.end_sec),
                               best.peak_sec, best.confidence, best.facts)
        else:
            merged.append(e)
    return sorted((e for e in merged if e.confidence >= cfg["min_confidence"]), key=lambda e: e.peak_sec)


def evidence_times(event: Event) -> list[float]:
    """Peak first, then interval start and end (1–3 distinct moments)."""
    times = [event.peak_sec]
    for t in (event.start_sec, event.end_sec):
        if len(times) < CONFIG["postprocess"]["max_evidence_frames"] and all(abs(t - x) > 0.5 for x in times):
            times.append(t)
    return times


def describe(event: Event, vtype: ViolationType, norm: Norm) -> str:
    """Observed facts only, then the rule — always "может указывать", never "нарушен закон"."""
    facts = {"start_sec": event.start_sec, "end_sec": event.end_sec,
             "duration_sec": event.end_sec - event.start_sec, **event.facts}
    return f"{vtype.description_template.format(**facts)} Это может указывать на нарушение требования: {norm.source}."
