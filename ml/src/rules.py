"""
rules.py — превращает ответы модели по кадрам (obs) в нарушения по критериям аналитиков.

obs = [{"t": 12.0, "f": {"cashier_at_desk": 1, "cash_on_desk": 1, ...}}, ...]  — 1 кадр в секунду

Посмотреть ответы модели и найденные нарушения по видео (без GPU, из кэша):
    python src/rules.py X-40
"""
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ml_config as cfg  # noqa: E402


def _flag(f, key):
    return bool(f.get(key, 0))


def _dt(obs):
    if len(obs) < 2:
        return 1.0
    return statistics.median(b["t"] - a["t"] for a, b in zip(obs, obs[1:])) or 1.0


def intervals(obs, cond, min_len, max_gap, dt):
    """Интервалы (начало, конец), где cond(f) истинно не меньше min_len секунд.
    Пропуски короче max_gap внутри интервала не разрывают его."""
    res, start, last = [], None, None
    for o in obs:
        if cond(o["f"]):
            if start is None:
                start = o["t"]
            last = o["t"]
        elif start is not None and o["t"] - last > max_gap + 1e-6:
            if last - start + dt >= min_len - 1e-6:
                res.append((start, last))
            start = None
    if start is not None and last - start + dt >= min_len - 1e-6:
        res.append((start, last))
    return res


def _onsets(obs, key, t_from, t_to):
    """Моменты, когда флаг key включается (был 0 -> стал 1) в промежутке [t_from, t_to]."""
    res, prev = [], False
    for o in obs:
        cur = _flag(o["f"], key)
        if cur and not prev and t_from <= o["t"] <= t_to:
            res.append(o["t"])
        prev = cur
    return res


def _any(obs, key, t_from, t_to):
    return any(_flag(o["f"], key) for o in obs if t_from <= o["t"] <= t_to)


def detect(obs, extra_rules=()):
    """Список событий: {"id", "t", "dur", "n"}. extra_rules — дополнительно включённые типы."""
    if not obs:
        return []
    P, dt = cfg.RULE_PARAMS, _dt(obs)
    events = []

    first_t = obs[0]["t"]

    def add(tid, cond, min_len, n=0):
        share = sum(1 for o in obs if cond(o["f"])) / len(obs)
        if share > P.get("background_ratio", 1.0) and cond(obs[0]["f"]):
            return  # видно с начала и почти всё видео — фон сцены (или постоянная ошибка), не событие
        for s, e in intervals(obs, cond, min_len, P["max_gap"], dt):
            if P.get("require_onset") and s <= first_t + 1e-6:
                continue  # было с самого начала видео — скорее фон сцены, чем событие
            events.append({"id": tid, "t": s, "dur": e - s + dt, "n": n})

    any_cash_work = any(_flag(o["f"], "cash_on_desk") or _flag(o["f"], "counting") for o in obs)

    # --- состояния ---
    add("V04", lambda f: _flag(f, "cash_on_desk") and not _flag(f, "cashier_at_desk"), P["unattended_sec"])
    add("V13", lambda f: _flag(f, "safe_open") and not _flag(f, "cashier_at_desk"), P["unattended_sec"])
    add("V13", lambda f: _flag(f, "safe_open"), P.get("safe_long_sec", 999))  # открыт слишком долго
    add("V14", lambda f: _flag(f, "keys_or_stamp") and not _flag(f, "cashier_at_desk"), P["unattended_sec"])
    add("V02", lambda f: _flag(f, "personal_bag"), P["bag_sec"])
    add("V01", lambda f: _flag(f, "phone"), P["phone_sec"])
    add("V16", lambda f: _flag(f, "outsider_in_cashier_zone"), P["outsider_sec"])
    add("V26", lambda f: _flag(f, "several_cash_stacks") and _flag(f, "cashier_at_desk"), P["stacks_sec"])
    add("V07", lambda f: not _flag(f, "cashier_at_desk") and _flag(f, "client_present")
        and not _flag(f, "cash_on_desk"), P["left_sec"])
    add("V05", lambda f: _flag(f, "counting") and not _flag(f, "client_present")
        and _flag(f, "cashier_at_desk"), P["count_no_client_sec"])
    add("V03", lambda f: _flag(f, "money_to_pocket"), dt)
    add("V19", lambda f: _flag(f, "bag_opened") and not _flag(f, "collector_present"),
        P.get("bag_opened_sec", dt))
    if any_cash_work:
        add("V15", lambda f: _flag(f, "door_open"), P["door_sec"])

    # --- операции с клиентом ---
    sessions = intervals(obs, lambda f: _flag(f, "client_present"), P["session_min_sec"], P["session_gap"], dt)
    for s, e in sessions:
        gives = _onsets(obs, "cashier_gives_money", s, e)
        takes = _onsets(obs, "client_gives_money", s, e)
        transfers = sorted(gives + takes + _onsets(obs, "money_transfer", s, e))

        # V12: была операция с деньгами (передача, деньги на столе или пересчёт при клиенте),
        #      документ за всё время клиента не показан
        cash_work = _any(obs, "cash_on_desk", s, e) or _any(obs, "counting", s, e)
        doc = _any(obs, "document_shown", s - 2 * dt, e + 2 * dt)
        if not doc and P.get("v12_det_id"):
            id_share = sum(_flag(o["f"], "id_detected") for o in obs) / len(obs)
            id_in_session = sum(_flag(o["f"], "id_detected") for o in obs if s <= o["t"] <= e + 2 * dt)
            doc = id_in_session >= 2 and id_share <= P.get("background_ratio", 0.7)
        if (transfers or cash_work) and not doc:
            t12 = transfers[0] if transfers else min(e, s + P.get("v12_offset", 5.0))
            events.append({"id": "V12", "t": t12, "dur": e - s + dt, "n": 0})

        # V08: выдача без пересчёта при клиенте перед ней
        for t in gives:
            if not _any(obs, "counting", max(s, t - P["recount_lookback"]), t - dt):
                events.append({"id": "V08", "t": t, "dur": dt, "n": 0})

        # V06 / V28: приём без пересчёта / пересчёт был, но не через счётную машину
        for t in takes:
            t_end = min(e + 5 * dt, t + P["accept_lookahead"])
            if not _any(obs, "counting", t + dt, t_end):
                events.append({"id": "V06", "t": t, "dur": dt, "n": 0})
            elif not _any(obs, "machine_counting", t, t_end):
                events.append({"id": "V28", "t": t, "dur": dt, "n": 0})

    # фильтры и слияние одинаковых рядом
    enabled = set(cfg.ENABLED_RULES) | set(extra_rules)
    events = [e for e in events if e["id"] in enabled and e["id"] not in cfg.VERDICT_EXCLUDE]
    events.sort(key=lambda e: e["t"])
    merged = []
    for e in events:
        if any(m["id"] == e["id"] and abs(m["t"] - e["t"]) < cfg.MERGE_SEC for m in merged):
            continue
        merged.append(e)
    # ограничение числа срабатываний одного типа (оставляем самые длительные)
    capped = []
    for tid in dict.fromkeys(e["id"] for e in merged):
        same = [e for e in merged if e["id"] == tid]
        cap = getattr(cfg, "PER_TYPE_MAX", {}).get(tid)
        if cap:
            same = sorted(same, key=lambda e: -e["dur"])[:cap]
        capped += same
    merged = sorted(capped, key=lambda e: e["t"])
    # если нарушений слишком много — оставляем самые длительные
    if len(merged) > cfg.MAX_RULE_VIOLATIONS:
        merged = sorted(merged, key=lambda e: -e["dur"])[:cfg.MAX_RULE_VIOLATIONS]
        merged.sort(key=lambda e: e["t"])
    return merged


def render_description(tid, t, dur, n=0):
    """Шаблон аналитиков с подстановкой + основание (норма)."""
    tpl = cfg.TYPES[tid].get("template", "")
    if dur is None:  # длительность неизвестна (режим lora) — убираем её из шаблона
        tpl = re.sub(r"\s*\(≈\{dur\} с\)", "", tpl).replace("≈{dur} с", "")
        dur = 1
    text = (tpl.replace("{t}", f"{t:.1f} с")
               .replace("{dur}", str(max(1, round(dur))))
               .replace("{n}", str(n)))
    ref = cfg.norm_reference(tid)
    return f"{text} Основание: {ref}." if ref else text


SHORT = {
    "cashier_at_desk": "CASH", "client_present": "CLNT", "outsider_in_cashier_zone": "OUTS",
    "collector_present": "COLL", "cash_on_desk": "MONY", "several_cash_stacks": "STCK",
    "keys_or_stamp": "KEYS", "personal_bag": "BAG", "phone": "PHON", "safe_open": "SAFE",
    "door_open": "DOOR", "counting": "CNT", "machine_counting": "MACH", "cashier_gives_money": "GIVE",
    "client_gives_money": "TAKE", "document_shown": "DOC", "money_to_pocket": "PCKT", "bag_opened": "BGOP",
}


def _short(key):
    return SHORT.get(key, key[:4].upper())


if __name__ == "__main__":
    from evaluate import norm_name
    name = norm_name(sys.argv[1])
    files = [p for p in Path(cfg.OBS_CACHE_DIR).glob("*.json") if norm_name(p.name) == name]
    if not files:
        sys.exit(f"Нет кэша ответов для {name} в {cfg.OBS_CACHE_DIR}")
    data = json.load(open(files[0], encoding="utf-8"))
    print("РОЛИ:", data["roles"], "\n")
    extra = ()
    det_file = Path(cfg.OBS_CACHE_DIR).parent / "det" / files[0].name
    if det_file.exists():  # подставить ответы детектора, как при анализе
        import detector
        det = json.load(open(det_file, encoding="utf-8"))["det"]
        data["obs"] = detector.apply(data["obs"], det)
        extra = cfg.DET_EXTRA_RULES
        print("Флаги с учётом детектора. Что детектор нашёл на кадрах:")
        for d in det:
            if d["found"]:
                print(f"  {d['t']:5.1f} с: " + ", ".join(f"{k}×{len(v)}" for k, v in d["found"].items()))
        print()
    keys = list(cfg.OBS_FIELDS)
    print("Сокращения:", ", ".join(f"{_short(k)}={k}" for k in keys), "\n")
    print("   t  " + " ".join(f"{_short(k):>4}" for k in keys))
    for o in data["obs"]:
        print(f"{o['t']:5.1f} " + " ".join(f"{'1' if o['f'].get(k) else '.':>4}" for k in keys))
    print("\nНАЙДЕНО:")
    for e in detect(data["obs"], extra):
        print(f"  {e['id']} {cfg.TYPES[e['id']]['type']} @ {e['t']:.1f} с (≈{e['dur']:.0f} с)")
