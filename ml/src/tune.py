"""
tune.py — подбор параметров правил и порогов детектора БЕЗ запуска моделей.

Берёт сохранённые ответы (outputs/obs — Qwen, outputs/det — детектор, outputs/lora_part — LoRA),
перебирает комбинации параметров и считает метрики в памяти (сотни вариантов за минуту).

    python src/tune.py                 # перебор и топ-10 вариантов
    python src/tune.py --show-errors   # разбор ошибок текущей конфигурации по видео

ВАЖНО: подбор идёт на тех же 40 видео, что и оценка, — это подгонка. Берите изменение, только если оно
даёт заметный выигрыш на НЕСКОЛЬКИХ видео, а не меняет результат одного.
"""
import argparse
import copy
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import detector  # noqa: E402
import ml_config as cfg  # noqa: E402
import rules  # noqa: E402
from evaluate import load_labels, norm_name  # noqa: E402

# Что перебираем: (где, ключ) -> варианты. Первое значение в списке не обязано быть текущим.
GRID = {
    ("rule", "unattended_sec"): [1.0, 2.0, 3.0],   # V04, V13, V14: сколько секунд без кассира
    ("rule", "safe_long_sec"): [8.0, 10.0, 15.0],  # V13: сейф открыт слишком долго
    ("rule", "outsider_sec"): [3.0, 5.0],          # V16: лишние V16 в X-2, X-15, X-19, X-38
    ("det", "Key"): [0.35, 0.5],                   # V14: пропущены X-22, X-24
    ("det", "Money"): [0.4, 0.5],                  # V04: пропущен X-15
    ("rule", "door_sec"): [6.0, 10.0, 15.0],      # V15 включён; сколько секунд дверь открыта
    ("rule", "v12_det_id"): [True, False],         # V12: учитывать удостоверение от детектора (свои видео)
    ("enable", "V16"): [True, False],              # посторонний: лишний в 4 видео и на своём own-3
}


def load_data(labels_path, truth):
    labels = {}
    for lp in labels_path.split(","):
        labels.update(load_labels(lp, truth))
    for dup in getattr(cfg, "DUPLICATES", {}):
        labels.pop(norm_name(dup), None)
    root = Path(cfg.OBS_CACHE_DIR).parent
    data = {}
    for p in Path(cfg.OBS_CACHE_DIR).glob("*.json"):
        name = norm_name(p.name)
        if name not in labels:
            continue
        obs = json.load(open(p, encoding="utf-8"))["obs"]
        det_p = root / "det" / p.name
        det = json.load(open(det_p, encoding="utf-8"))["det"] if det_p.exists() else None
        lora_p = root / "lora_part" / p.name
        lora = json.load(open(lora_p, encoding="utf-8")) if lora_p.exists() else {"violations": []}
        data[name] = {"obs": obs, "det": det, "lora": lora, "gt": labels[name]}
    return data


def merge(lora_v, rule_v):
    """Та же логика, что model.merge_results (без кадров)."""
    out = list(lora_v)
    if cfg.ENSEMBLE_MODE == "fallback" and out:
        rule_v = []
    for v in rule_v:
        if cfg.ENSEMBLE_MODE == "suppress_near" and any(abs(o["t"] - v["t"]) < cfg.ENSEMBLE_SUPPRESS_SEC
                                                        for o in lora_v):
            continue
        if all(not (o["id"] == v["id"] and abs(o["t"] - v["t"]) < cfg.MERGE_SEC) for o in out):
            out.append(v)
    return sorted(out, key=lambda v: v["t"])[:cfg.MAX_RULE_VIOLATIONS]


_APPLIED = {}


def predict(item, names, name=None):
    if item["det"]:
        k = (name, tuple(sorted(cfg.DET_THRESHOLDS.items())))  # подстановка детектора зависит только от порогов
        if name is None or k not in _APPLIED:
            _APPLIED[k] = detector.apply(item["obs"], item["det"], names)
        obs = _APPLIED[k]
    else:
        obs = item["obs"]
    extra = cfg.DET_EXTRA_RULES if item["det"] else ()
    rule_v = [{"id": e["id"], "t": e["t"]} for e in rules.detect(obs, extra)]
    lora_v = [{"id": cfg.NAME_TO_ID.get(v["type"], v["type"]), "t": v["timecode_sec"]}
              for v in item["lora"].get("violations", [])]
    return merge(lora_v, rule_v)


def score(data, names):
    m = {"ok": 0, "n": 0, "fp": 0, "fn": 0, "pred": 0, "pred_ok": 0, "pairs": 0, "pairs_ok": 0}
    per_video = {}
    for name, item in data.items():
        pv = predict(item, names, name)
        gt = item["gt"]
        gt_types = {t for t in gt["types"] if t != "V11"}
        said = bool(pv)
        m["n"] += 1
        m["ok"] += said == gt["violation"]
        m["fp"] += said and not gt["violation"]
        m["fn"] += gt["violation"] and not said
        ids = {v["id"] for v in pv}
        m["pred"] += len(pv)
        m["pred_ok"] += sum(v["id"] in gt_types for v in pv)
        m["pairs"] += len(gt_types)
        m["pairs_ok"] += len(gt_types & ids)
        per_video[name] = pv
    return m, per_video


def fmt(m):
    prec = m["pred_ok"] / m["pred"] if m["pred"] else 0
    rec = m["pairs_ok"] / m["pairs"] if m["pairs"] else 0
    return (f"вердикт {m['ok']}/{m['n']} ({100 * m['ok'] / m['n']:.0f}%) | пропусков {m['fn']} ложных {m['fp']} | "
            f"типы: точность {100 * prec:.0f}% полнота {100 * rec:.0f}%")


BASE_ENABLED = list(cfg.ENABLED_RULES)


def apply_params(params):
    cfg.ENABLED_RULES = list(BASE_ENABLED)
    for (where, key), val in params.items():
        if where == "rule":
            cfg.RULE_PARAMS[key] = val
        elif where == "det":
            cfg.DET_THRESHOLDS[key] = val
        elif where == "enable":
            if val and key not in cfg.ENABLED_RULES:
                cfg.ENABLED_RULES.append(key)
            if not val and key in cfg.ENABLED_RULES:
                cfg.ENABLED_RULES.remove(key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="data/labels.csv",
                    help="можно несколько через запятую, например data/labels.csv,data/labels_own.csv")
    ap.add_argument("--truth", default="analyst")
    ap.add_argument("--show-errors", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    data = load_data(args.labels, args.truth)
    names = detector.class_names() if detector.available() else set()
    print(f"Видео с сохранёнными ответами: {len(data)}. Детектор: {'да' if names else 'нет'}\n")

    base_rule, base_det = copy.deepcopy(cfg.RULE_PARAMS), copy.deepcopy(cfg.DET_THRESHOLDS)
    base_m, base_pv = score(data, names)
    print("ТЕКУЩАЯ КОНФИГУРАЦИЯ:", fmt(base_m))

    if args.show_errors:
        print("\nОшибки по видео (разметка -> ответ):")
        for name in sorted(data, key=lambda n: int(n.split("-")[1])):
            gt, pv = data[name]["gt"], base_pv[name]
            gt_types = {t for t in gt["types"] if t != "V11"}
            ids = {v["id"] for v in pv}
            if gt["violation"] and not pv:
                tag = "ПРОПУСК"
            elif not gt["violation"] and pv:
                tag = "ЛОЖНОЕ"
            elif gt["violation"] and not (gt_types & ids):
                tag = "ТИП НЕ ТОТ"
            else:
                continue
            gts = ", ".join(sorted(gt_types)) or "чисто"
            got = ", ".join(f"{v['id']}@{v['t']:g}" for v in pv) or "—"
            print(f"  {name:<6} {tag:<11} разметка: {gts:<12} ответ: {got}")
        return

    keys = list(GRID)
    results = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        p = dict(zip(keys, combo))
        cfg.RULE_PARAMS.update(base_rule)
        cfg.DET_THRESHOLDS.clear()
        cfg.DET_THRESHOLDS.update(base_det)
        params = dict(zip(keys, combo))
        apply_params(params)
        m, pv = score(data, names)
        changed = sum(1 for n in data if {v["id"] for v in pv[n]} != {v["id"] for v in base_pv[n]})
        rec = m["pairs_ok"] / m["pairs"] if m["pairs"] else 0
        prec = m["pred_ok"] / m["pred"] if m["pred"] else 0
        results.append(((m["ok"], -m["fp"], rec, prec), params, m, changed))
    cfg.RULE_PARAMS.update(base_rule)
    cfg.DET_THRESHOLDS.clear()
    cfg.DET_THRESHOLDS.update(base_det)
    cfg.ENABLED_RULES = list(BASE_ENABLED)

    results.sort(key=lambda r: r[0], reverse=True)
    print(f"\nПеребрано вариантов: {len(results)}. Лучшие (вердикт, затем меньше ложных, затем типы):\n")
    for _, params, m, changed in results[:args.top]:
        def base_val(k):
            if k[0] == "rule":
                return base_rule.get(k[1])
            if k[0] == "det":
                return base_det.get(k[1], base_det["default"])
            return k[1] in BASE_ENABLED
        diff = ", ".join((f"{'вкл' if v else 'выкл'} {k[1]}" if k[0] == "enable" else f"{k[1]}={v:g}")
                         for k, v in params.items() if base_val(k) != v)
        print(f"  {fmt(m)} | изменено видео: {changed} | {diff or 'как сейчас'}")
    print("\nЕсли лучший вариант меняет результат 1–2 видео — это, скорее всего, подгонка; не берите.")


if __name__ == "__main__":
    main()
