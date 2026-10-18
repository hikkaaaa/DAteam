#!/bin/bash
# setup_server.sh — один раз после аренды сервера.
# Запуск: bash setup_server.sh
set -e

echo "=== 1. Проверка видеокарты ==="
nvidia-smi

echo "=== 2. ffmpeg (запасной инструмент для видео) ==="
(apt-get update -qq && apt-get install -y -qq ffmpeg) || echo "ffmpeg не поставился — не критично"

echo "=== 3. Библиотеки (torch уже есть в шаблоне PyTorch, его не трогаем) ==="
pip install -q -U "transformers>=4.57" accelerate qwen-vl-utils opencv-python-headless pillow huggingface_hub

echo "=== 4. Скачивание Qwen3-VL-8B-Instruct (~17 ГБ) ==="
mkdir -p /workspace/models
python - <<'EOF'
from huggingface_hub import snapshot_download
snapshot_download("Qwen/Qwen3-VL-8B-Instruct", local_dir="/workspace/models/qwen3-vl-8b")
print("Модель скачана")
EOF

echo "=== 5. Проверка ==="
python -c "import torch, transformers; print('CUDA:', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0)); print('transformers', transformers.__version__)"
echo "Готово."
