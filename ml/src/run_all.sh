#!/bin/bash
# run_all.sh — всё по порядку:
#   дождаться текущего прогона -> оценка checklist -> peft -> проверка обучения -> дообучение
#   -> прогон дообученной модели на отложенных видео -> сравнение двух режимов
# Запуск:  nohup bash src/run_all.sh > all.log 2>&1 &     смотреть:  tail -f all.log
set -e
cd /workspace/datathon
mkdir -p outputs

# что бы ни случилось — в конце вернуть основной режим checklist
trap 'sed -i "s/^PIPELINE = \"lora\"/PIPELINE = \"checklist\"/" src/ml_config.py' EXIT

echo "=== 0. Жду окончания текущего прогона (если идёт) === $(date +%H:%M)"
while pgrep -f "src/run_dataset.py" > /dev/null; do sleep 30; done

echo "=== 1. Оценка режима checklist на всех видео === $(date +%H:%M)"
python src/evaluate.py > outputs/eval_checklist_all.txt
grep -A12 "ИТОГ" outputs/eval_checklist_all.txt

echo "=== 2. Установка peft === $(date +%H:%M)"
pip install -q peft

echo "=== 3. Обучающие примеры === $(date +%H:%M)"
python src/train_lora.py --dry-run

echo "=== 4. Быстрая проверка обучения === $(date +%H:%M)"
python src/train_lora.py --smoke

echo "=== 5. Дообучение (30–60 минут) === $(date +%H:%M)"
python src/train_lora.py

echo "=== 6. Дообученная модель на отложенных видео === $(date +%H:%M)"
sed -i 's/^PIPELINE = "checklist"/PIPELINE = "lora"/' src/ml_config.py
python src/run_dataset.py --only val --out outputs/preds_lora --raw outputs/raw_lora
sed -i 's/^PIPELINE = "lora"/PIPELINE = "checklist"/' src/ml_config.py

echo "=== 7. Сравнение на отложенных видео === $(date +%H:%M)"
python src/evaluate.py --only val --preds outputs/preds_lora > outputs/eval_lora_val.txt
python src/evaluate.py --only val > outputs/eval_checklist_val.txt
echo "------------------------- LoRA (дообученная) -------------------------"
cat outputs/eval_lora_val.txt
echo "------------------------- checklist ----------------------------------"
cat outputs/eval_checklist_val.txt
echo "=== ГОТОВО === $(date +%H:%M)"
