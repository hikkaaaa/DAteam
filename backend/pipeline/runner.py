"""Video file -> violations for the API + optional details for the UI (pipeline from CLAUDE.md)."""

from collections.abc import Callable
from pathlib import Path

from config import CONFIG
from pipeline.events import describe, evidence_times, find_events, merge_events
from pipeline.model import Detector, Verifier, build_scene_state
from pipeline.video import duration_sec, encode_jpeg, iter_frames, read_frames_at
from rules import load_active_rules
from schemas import EvidenceFrame, Violation


def analyze_video(video_path: Path, start_sec: float | None = None, end_sec: float | None = None,
                  on_progress: Callable[[float, str], None] = lambda p, stage: None,
                  ) -> tuple[list[Violation], list[dict]]:
    """Analyze the whole video, or only [start_sec, end_sec] if given.

    Returns (violations, details):
      violations — exactly the ТЗ format;
      details    — one dict per violation (same order) with UI-only extras: confidence, interval,
                   the norm with its verbatim quote. Served by the optional /details endpoint.
    `on_progress(0..1, stage)` drives the UI's step-by-step progress: detect -> rules -> report.
    """
    seg_start = start_sec or 0.0
    seg_end = min(end_sec or float("inf"), duration_sec(video_path)) or 1.0
    types, norms = load_active_rules()
    detector, verifier = Detector(), Verifier()  # TODO(ml): create once at startup instead

    # 1–3. Decode, detect + track, scene state — one streaming pass over the video.
    states = []
    for frame in iter_frames(video_path, CONFIG["video"]["sample_fps"], CONFIG["video"]["max_side_px"],
                             start_sec, end_sec):
        states.append(build_scene_state(frame.timestamp_sec, detector.detect(frame)))
        on_progress(0.85 * min(1.0, (frame.timestamp_sec - seg_start) / max(seg_end - seg_start, 1e-6)), "detect")

    # 4–6. Candidate events -> VLM check -> merge + confidence filter.
    on_progress(0.85, "rules")
    candidates = find_events(states, types)
    verified = [e for e in candidates
                if verifier.confirms(video_path, e.start_sec, e.end_sec, types[e.type_id].criterion)]

    # 7. Description from facts + norms.yaml, 1–3 evidence frames.
    on_progress(0.9, "report")
    events = merge_events(verified)
    violations, details = [], []
    for i, event in enumerate(events):
        vtype = types[event.type_id]
        norm = norms[vtype.norms[0]]
        frames = read_frames_at(video_path, evidence_times(event))
        violations.append(Violation(
            type=vtype.type,
            timecode_sec=round(event.peak_sec, 2),
            description=describe(event, vtype, norm),
            evidence_frames=[EvidenceFrame(timestamp_sec=round(f.timestamp_sec, 2),
                                           image_base64=encode_jpeg(f.image, CONFIG["postprocess"]["evidence_jpeg_quality"]))
                             for f in frames],
        ))
        details.append({
            "confidence": round(event.confidence, 3),
            "start_sec": round(event.start_sec, 2),
            "end_sec": round(event.end_sec, 2),
            "rule": norm.public(),  # document, clause, verbatim quote, url, ...
            "other_norms": [norms[n].public() for n in vtype.norms[1:] if n in norms],
        })
        on_progress(0.9 + 0.1 * (i + 1) / len(events), "report")
    return violations, details
