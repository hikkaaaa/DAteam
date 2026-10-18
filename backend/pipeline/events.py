"""Scene timeline -> violation events: rule detectors, merging, descriptions.

To add a violation type: write a detector below, register it in DETECTORS under the type's `key`
from rules/violation_types.yaml, and put its thresholds under `detectors.<key>` in config.yaml.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from config import CONFIG
from pipeline.model import SceneState
from rules import Norm, ViolationType


@dataclass
class Event:
    type_id: str          # V01, V02, ... from rules/violation_types.yaml (set by find_events)
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

def detect_cash_left_unattended(states: list[SceneState], params: dict) -> list[Event]:
    """V04 `cash_left_unattended` (черновик): cash stays on the desk while the cashier is away."""
    def condition(s: SceneState) -> bool | None:
        if s.cash_on_desk is None or s.cashier_at_desk is None:
            return None
        return s.cash_on_desk and not s.cashier_at_desk

    # TODO(ml): replace the fixed confidence with a real score (e.g. mean detector confidence).
    return [Event("", a, b, (a + b) / 2, confidence=0.7)
            for a, b in find_intervals(states, condition, params["min_duration_sec"])]


# Detector name (`key` in rules/violation_types.yaml) -> function.
# TODO(ml): add a detector for every key in rules/violation_types.yaml (28 types, all «черновик» for now).
DETECTORS: dict[str, Callable[[list[SceneState], dict], list[Event]]] = {
    "cash_left_unattended": detect_cash_left_unattended,
}


def find_events(states: list[SceneState], types: dict[str, ViolationType]) -> list[Event]:
    events = []
    for type_id, vtype in types.items():
        detector = DETECTORS.get(vtype.key)
        if detector is None:
            continue
        for event in detector(states, CONFIG["detectors"].get(vtype.key, {})):
            event.type_id = type_id
            events.append(event)
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


class _Facts(dict):
    def __missing__(self, key):  # an unknown placeholder never crashes the job
        return "?"


def describe(event: Event, vtype: ViolationType, norm: Norm) -> str:
    """Template from violation_types.yaml (observed facts + "может указывать на …"), then the norm.
    Placeholders: {t} start time (m:ss), {dur} duration in seconds, {n} people count (from detector facts)."""
    start = int(event.start_sec)
    facts = _Facts(t=f"{start // 60}:{start % 60:02d}", dur=round(event.end_sec - event.start_sec), **event.facts)
    return f"{vtype.template.format_map(facts)} Норма: {norm.source}."
