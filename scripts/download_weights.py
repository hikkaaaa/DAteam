"""Download all model weights into weights/ ONCE, before running the service.

This is the only place allowed to touch the network. At runtime the service
loads models from local paths only (HF_HUB_OFFLINE=1).

Usage:
    python scripts/download_weights.py
"""

from pathlib import Path

WEIGHTS_DIR = Path(__file__).resolve().parents[1] / "weights"

# TODO(ml): list every model the pipeline needs. Paths must match config.yaml.
#   ("detector.pt", "https://.../yolo11m.pt")                -> detection.weights_path
#   ("vlm", "Qwen/Qwen2.5-VL-3B-Instruct")                    -> vlm.weights_path
FILE_WEIGHTS: list[tuple[str, str]] = []      # (file name in weights/, direct URL)
HF_REPOS: list[tuple[str, str]] = []          # (folder name in weights/, Hugging Face repo id)


def main() -> None:
    WEIGHTS_DIR.mkdir(exist_ok=True)

    for name, url in FILE_WEIGHTS:
        target = WEIGHTS_DIR / name
        if target.exists():
            print(f"skip  {name} (exists)")
            continue
        import urllib.request
        print(f"get   {name} <- {url}")
        urllib.request.urlretrieve(url, target)

    for name, repo_id in HF_REPOS:
        from huggingface_hub import snapshot_download
        print(f"get   {name} <- hf:{repo_id}")
        snapshot_download(repo_id=repo_id, local_dir=WEIGHTS_DIR / name)

    if not FILE_WEIGHTS and not HF_REPOS:
        print("No weights configured yet — edit FILE_WEIGHTS / HF_REPOS in this script.")


if __name__ == "__main__":
    main()
