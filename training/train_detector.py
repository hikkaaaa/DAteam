"""Train / fine-tune the object detector. Output goes to weights/detector.pt,
which backend/pipeline/model.py loads at runtime.

Usage (from the repo root):
    python training/train_detector.py --data data/detector/dataset.yaml --epochs 50
"""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# TODO(data): prepare the dataset in YOLO format, e.g. data/detector/:
#   images/{train,val}/*.jpg  — frames extracted from the training videos
#   labels/{train,val}/*.txt  — one "class x_center y_center w h" line per box (normalized)
#   dataset.yaml              — paths + class names; must match `detection.classes` in config.yaml
# Split by VIDEO, not by frame (frames of one video never in both train and val).
# Never use the jury test videos.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True, help="YOLO dataset.yaml")
    parser.add_argument("--base", default="weights/yolo_base.pt", help="pretrained weights to start from")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=1280)
    args = parser.parse_args()

    # TODO(ml): uncomment once ultralytics is installed (see backend/requirements.txt).
    #   from ultralytics import YOLO
    #   model = YOLO(str(ROOT / args.base))
    #   result = model.train(data=str(args.data), epochs=args.epochs, imgsz=args.imgsz,
    #                        project=str(ROOT / "training/runs"), name="detector")
    #   shutil.copy(Path(result.save_dir) / "weights/best.pt", ROOT / "weights/detector.pt")
    #   print("Saved weights/detector.pt — now run scripts/evaluate.py")
    _ = shutil
    raise NotImplementedError("Detector training is not wired yet — see TODO(ml) above")


if __name__ == "__main__":
    main()
