"""
training_report.py — графики и сводка по ОБУЧЕНИЮ моделей (не по результатам на видео).

Читает логи обучения:
    train_all.log      — финальное дообучение LoRA (Qwen3-VL) на всех видео
    all.log            — первое дообучение LoRA (34 видео, 8 отложены)        (если есть)
    det_objects.log    — обучение детектора предметов RT-DETR
    det_people.log     — обучение детектора людей и действий                  (если есть)

    python src/training_report.py
Результат:
    outputs/training_report.png   — графики: loss LoRA, loss детектора, F1 детектора по эпохам
    outputs/training_summary.md   — сводка: данные, параметры, итоговые метрики по классам
"""
import re
import sys
from pathlib import Path

LORA_RE = re.compile(r"эпоха (\d+)/(\d+) шаг (\d+)/(\d+) loss ([\d.]+)")
DET_EPOCH_RE = re.compile(r"^эпоха (\d+)/(\d+) loss ([\d.]+)")
DET_F1_RE = re.compile(r"^\s*средний F1 ([\d.]+)")
DET_CLASS_RE = re.compile(r"^\s+(.+?)\s+найдено\s+(\d+)\s+пропущено\s+(\d+)\s+лишних\s+(\d+)\s+\|\s+точность ([\d.]+) полнота ([\d.]+)")


def read(path):
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="ignore").splitlines() if p.exists() else []


def parse_lora(lines):
    steps, losses, info = [], [], {}
    for ln in lines:
        m = LORA_RE.search(ln)
        if m:
            steps.append(int(m.group(3)))
            losses.append(float(m.group(5)))
        if ln.startswith("Примеров:"):
            info["examples"] = ln.strip()
        if "trainable params" in ln:
            info["params"] = ln.strip()
        if ln.startswith("Готово за"):
            info["done"] = ln.strip()
    return steps, losses, info


def parse_det(lines):
    epochs, losses, f1_epochs, f1s, classes, info = [], [], [], [], {}, {}
    last_epoch = 0
    block = {}
    for ln in lines:
        m = DET_EPOCH_RE.search(ln)
        if m:
            last_epoch = int(m.group(1))
            epochs.append(last_epoch)
            losses.append(float(m.group(3)))
        m = DET_CLASS_RE.search(ln)
        if m:
            block[m.group(1).strip()] = {"tp": int(m.group(2)), "fn": int(m.group(3)), "fp": int(m.group(4)),
                                         "p": float(m.group(5)), "r": float(m.group(6))}
        m = DET_F1_RE.search(ln)
        if m:
            f1_epochs.append(last_epoch)
            f1s.append(float(m.group(1)))
            classes = block
            block = {}
        if ln.startswith("Картинок:"):
            info["images"] = ln.strip()
        if ln.startswith("Базовая модель:"):
            info["base"] = ln.strip()
        if ln.startswith("Готово за"):
            info["done"] = ln.strip()
    return epochs, losses, f1_epochs, f1s, classes, info


def main():
    lora_final = parse_lora(read("train_all.log"))
    lora_first = parse_lora(read("all.log"))
    dets = {"предметы": parse_det(read("det_objects.log")), "люди и действия": parse_det(read("det_people.log"))}
    dets = {k: v for k, v in dets.items() if v[0]}
    if not lora_final[0] and not lora_first[0] and not dets:
        sys.exit("Не найдено логов обучения (train_all.log, all.log, det_objects.log) в текущей папке")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("Нет matplotlib: pip install -q matplotlib")

    n = 1 + 2 * bool(dets)
    fig, axes = plt.subplots(1, n, figsize=(6 * n, 4.5))
    axes = [axes] if n == 1 else list(axes)

    ax = axes[0]
    if lora_first[0]:
        ax.plot(lora_first[0], lora_first[1], "o-", alpha=0.6, label="LoRA: 34 видео (8 отложены)")
    if lora_final[0]:
        ax.plot(lora_final[0], lora_final[1], "o-", label="LoRA: все видео (финальная)")
    ax.set_title("Дообучение Qwen3-VL (LoRA): loss")
    ax.set_xlabel("шаг обучения")
    ax.set_ylabel("loss")
    ax.grid(alpha=0.3)
    ax.legend()

    if dets:
        ax = axes[1]
        for name, (ep, ls, *_rest) in dets.items():
            ax.plot(ep, ls, "-", label=f"детектор: {name}")
        ax.set_title("Детектор RT-DETR: loss")
        ax.set_xlabel("эпоха")
        ax.grid(alpha=0.3)
        ax.legend()
        ax = axes[2]
        for name, (_e, _l, fe, f1, *_rest) in dets.items():
            ax.plot(fe, f1, "o-", label=f"детектор: {name}")
        ax.set_title("Детектор: средний F1 на проверочных кадрах")
        ax.set_xlabel("эпоха")
        ax.set_ylim(0, 1)
        ax.grid(alpha=0.3)
        ax.legend()

    fig.tight_layout()
    out_dir = Path("outputs")
    out_dir.mkdir(exist_ok=True)
    fig.savefig(out_dir / "training_report.png", dpi=130)

    md = ["# Обучение моделей — сводка", ""]
    md += ["## 1. Дообучение Qwen3-VL-8B методом LoRA", "",
           "- Метод: LoRA (ранг 16, alpha 32, dropout 0.05), визуальная часть заморожена, "
           "дообучалась языковая часть; 3 эпохи, lr 1e-4, 8 примеров на шаг.",
           "- Пример = окно 8 с (8 кадров с подписанным временем) → JSON с ID нарушения и секундой."]
    for title, (st, ls, info) in (("Первое обучение (34 видео, 8 отложены для честной проверки)", lora_first),
                                  ("Финальное обучение (все видео, без дубликатов)", lora_final)):
        if st:
            md += ["", f"**{title}**", ""]
            md += [f"- {v}" for v in info.values()]
            md += [f"- loss: {ls[0]:.3f} (шаг {st[0]}) → {ls[-1]:.3f} (шаг {st[-1]})"]
    for name, (ep, ls, fe, f1, classes, info) in dets.items():
        md += ["", f"## Детектор ({name}) — RT-DETR, transfer learning", ""]
        md += [f"- {v}" for v in info.values()]
        if ls:
            md += [f"- loss: {ls[0]:.2f} (эпоха {ep[0]}) → {ls[-1]:.2f} (эпоха {ep[-1]})"]
        if f1:
            md += [f"- средний F1 на проверочных кадрах: {f1[0]:.3f} (эпоха {fe[0]}) → лучший {max(f1):.3f}"]
        if classes:
            md += ["", "| Класс | Точность | Полнота | Найдено | Пропущено | Лишних |", "|---|---|---|---|---|---|"]
            for c, s in sorted(classes.items()):
                md += [f"| {c} | {s['p']:.2f} | {s['r']:.2f} | {s['tp']} | {s['fn']} | {s['fp']} |"]
    md += ["", "Графики: `outputs/training_report.png`. Метрики детектора — на проверочной части разметки "
               "(кадры из тех же видео, поэтому оценка оптимистичная)."]
    (out_dir / "training_summary.md").write_text("\n".join(md), encoding="utf-8")
    print("Готово: outputs/training_report.png и outputs/training_summary.md")


if __name__ == "__main__":
    main()
