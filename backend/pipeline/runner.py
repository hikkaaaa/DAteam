"""Video file -> list of violations for the API (pipeline from CLAUDE.md)."""

from pathlib import Path

from config import CONFIG
from pipeline.events import describe, evidence_times, find_events, merge_events
from pipeline.model import Detector, Verifier, build_scene_state
from pipeline.video import encode_jpeg, iter_frames, read_frames_at
from rules import load_active_rules
from schemas import EvidenceFrame, Violation


def analyze_video(video_path: Path) -> list[Violation]:
    types, norms = load_active_rules()
    detector, verifier = Detector(), Verifier()  # TODO(ml): create once at startup instead

    # 1–3. Decode, detect + track, scene state — one streaming pass over the video.
    states = [
        build_scene_state(frame.timestamp_sec, detector.detect(frame))
        for frame in iter_frames(video_path, CONFIG["video"]["sample_fps"], CONFIG["video"]["max_side_px"])
    ]

    # 4–6. Candidate events -> VLM check -> merge + confidence filter.
    candidates = find_events(states, set(types))
    verified = [e for e in candidates
                if verifier.confirms(video_path, e.start_sec, e.end_sec, types[e.type_id].visual_criterion)]

    # 7. Description from facts + norms.yaml, 1–3 evidence frames.
    violations = []
    for event in merge_events(verified):
        vtype = types[event.type_id]
        norm = norms[vtype.rule_id]
        frames = read_frames_at(video_path, evidence_times(event))
        violations.append(Violation(
            type=vtype.type,
            timecode_sec=round(event.peak_sec, 2),
            description=describe(event, vtype, norm),
            evidence_frames=[EvidenceFrame(timestamp_sec=round(f.timestamp_sec, 2),
                                           image_base64=encode_jpeg(f.image, CONFIG["postprocess"]["evidence_jpeg_quality"]))
                             for f in frames],
        ))
    return violations
