"""ML models — THE place to plug them in. All three are stubs for now.

  Detector           YOLO + ByteTrack/BoT-SORT: objects with track IDs on each frame
  build_scene_state  detections -> facts about the scene ("cashier at desk", "cash on desk")
  Verifier           local VLM: re-checks each candidate event to cut false positives

Models load ONLY from local paths in weights/ (see scripts/download_weights.py).
No cloud APIs, no downloads at runtime.
"""

from dataclasses import dataclass, field
from pathlib import Path

from config import CONFIG, path
from pipeline.video import Frame, read_frames_at


@dataclass
class Detection:
    class_name: str                          # one of config.yaml detection.classes
    confidence: float
    bbox: tuple[float, float, float, float]  # normalized x1, y1, x2, y2
    track_id: int | None = None


@dataclass
class SceneState:
    """The scene at one moment. None = unknown: rule detectors never fire on unknowns."""
    timestamp_sec: float
    cashier_at_desk: bool | None = None
    cash_on_desk: bool | None = None
    safe_open: bool | None = None
    phone_in_use: bool | None = None
    extra: dict = field(default_factory=dict)


# ---------- 1. Detection + tracking ----------

class Detector:
    def __init__(self) -> None:
        self.cfg = CONFIG["detection"]
        self.model = None
        # TODO(ml): load local weights (path from config.yaml):
        #   from ultralytics import YOLO
        #   self.model = YOLO(str(path(self.cfg["weights_path"])))

    def detect(self, frame: Frame) -> list[Detection]:
        """Call on frames in chronological order so track IDs stay consistent."""
        if self.model is None:
            return []
        # TODO(ml):
        #   r = self.model.track(frame.image, persist=True, tracker=self.cfg["tracker"],
        #                        conf=self.cfg["confidence_threshold"], iou=self.cfg["iou_threshold"],
        #                        device=self.cfg["device"], verbose=False)[0]
        #   return [Detection(r.names[int(b.cls)], float(b.conf), tuple(b.xyxyn[0].tolist()),
        #                     int(b.id) if b.id is not None else None) for b in r.boxes]
        raise NotImplementedError


# ---------- 2. Scene state ----------

def build_scene_state(timestamp_sec: float, detections: list[Detection]) -> SceneState:
    state = SceneState(timestamp_sec)
    # TODO(ml): derive facts from detections + zones in config.yaml (scene.zones), e.g.
    #   cashier_at_desk = a person's box overlaps the cash_desk zone
    #   cash_on_desk    = a cash / cash_bundle box lies inside the cash_desk zone
    #   safe_open       = a safe / drawer box classified as open
    #   phone_in_use    = a phone box overlaps the cashier's hand
    # Smooth noisy per-frame values over a short window before trusting them.
    return state


# ---------- 3. VLM verification ----------

class Verifier:
    def __init__(self) -> None:
        self.cfg = CONFIG["vlm"]
        self.model = None
        if self.cfg["enabled"]:
            # TODO(ml): load the VLM from local files only:
            #   from transformers import AutoProcessor, AutoModelForVision2Seq
            #   p = path(self.cfg["weights_path"])
            #   self.processor = AutoProcessor.from_pretrained(p, local_files_only=True)
            #   self.model = AutoModelForVision2Seq.from_pretrained(p, local_files_only=True).to("cuda")
            raise NotImplementedError("vlm.enabled is true in config.yaml but the VLM isn't wired yet")

    def confirms(self, video_path: Path, start_sec: float, end_sec: float, question: str) -> bool:
        """True if the VLM sees the event on enough frames of [start, end]. Passes everything when off."""
        if self.model is None:
            return True
        n = self.cfg["frames_per_candidate"]
        times = [start_sec + i * (end_sec - start_sec) / max(n - 1, 1) for i in range(n)]
        frames = read_frames_at(video_path, times, CONFIG["video"]["max_side_px"])
        # TODO(ml): ask `question` (a yes/no question built from the type's visual_criterion)
        #   on each frame; return share_of_yes >= self.cfg["min_agreement"].
        raise NotImplementedError
