from pathlib import Path
import json
import math
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
MATCH_IDS = ["8825993964", "8825996999", "8826042346", "8826052816", "8826099852"]
COMBAT_LABELS = {"小规模冲突", "团战", "肉山团", "高地团", "守塔团", "魔晶团"}


def split_labels(value):
    return [label.strip() for label in re.split(r"\s+/\s+", str(value or "")) if label.strip()]


def time_to_seconds(value):
    minutes, seconds = str(value).split(":", 1)
    return int(minutes) * 60 + int(seconds)


def parse_range(value):
    start, end = str(value).split("-", 1)
    return time_to_seconds(start), time_to_seconds(end)


def gap_seconds(a, b):
    ar = parse_range(a["time_range"])
    br = parse_range(b["time_range"])
    return max(0, br[0] - ar[1])


def overlap_seconds(a, b):
    ar = parse_range(a["time_range"])
    br = parse_range(b["time_range"])
    return max(0, min(ar[1], br[1]) - max(ar[0], br[0]))


def side_heroes(heroes_text, side):
    match = re.search(rf"{side}[:：]\s*([^；;]+)", str(heroes_text or ""))
    if not match:
        return set()
    text = match.group(1).strip()
    if not text or text == "无":
        return set()
    return {item.strip() for item in re.split(r"[、,，]\s*", text) if item.strip() and item.strip() != "无"}


def all_heroes(row):
    return side_heroes(row.get("heroes", ""), "天辉") | side_heroes(row.get("heroes", ""), "夜魇")


def hero_jaccard(a, b):
    ah = all_heroes(a)
    bh = all_heroes(b)
    if not ah or not bh:
        return 0.0
    return len(ah & bh) / len(ah | bh)


def parse_game_position(text):
    match = re.search(r"game=\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)", str(text or ""))
    if match:
        return float(match.group(1)), float(match.group(2))
    raw = re.search(r"raw=\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)", str(text or ""))
    if raw:
        return float(raw.group(1)) * 130, float(raw.group(2)) * 130
    return None


def distance(a, b):
    if not a or not b:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def has_combat_label(row):
    return bool(COMBAT_LABELS & set(split_labels(row.get("labels"))))


def death_names(row):
    match = re.search(r"死亡：(.+)", str(row.get("结果", "")))
    if not match:
        return set()
    value = match.group(1)
    if value.strip() == "无":
        return set()
    names = set()
    for item in re.split(r"[、,，]\s*", value):
        item = item.strip()
        m = re.search(r"\d+:\d+\s+(.+)$", item)
        names.add((m.group(1) if m else item).strip())
    return names


def has_kill(row):
    return bool(death_names(row)) or ("击杀结果：" in str(row.get("结果", "")) and "未记录英雄击杀" not in str(row.get("结果", "")) and "0 - 0" not in str(row.get("结果", "")))


def load_supplements(match_id):
    path = OUTPUTS / f"tactical_events_{match_id}" / f"tactical_events_{match_id}_v1.5.json"
    full_rows = json.loads(path.read_text(encoding="utf-8"))
    by_time = {}
    for row in full_rows:
        by_time.setdefault(row.get("time_range"), []).append(row)
    return by_time


def attach_positions(match_id, rows):
    by_time = load_supplements(match_id)
    for row in rows:
        candidates = by_time.get(row.get("time_range"), [])
        text = ""
        if candidates:
            text = " ".join(str(candidates[0].get(key, "")) for key in ("region", "evidence"))
        row["_pos"] = parse_game_position(text)
        row["_evidence"] = candidates[0].get("evidence", "") if candidates else ""
        row["_region"] = candidates[0].get("region", "") if candidates else ""


def classify_pair(left, right):
    gap = gap_seconds(left, right)
    overlap = overlap_seconds(left, right)
    jaccard = hero_jaccard(left, right)
    shared = sorted(all_heroes(left) & all_heroes(right))
    dist = distance(left.get("_pos"), right.get("_pos"))
    left_deaths = death_names(left)
    right_deaths = death_names(right)
    same_death = bool(left_deaths and right_deaths and left_deaths & right_deaths)
    one_no_kill = has_kill(left) != has_kill(right)
    both_no_kill = not has_kill(left) and not has_kill(right)

    reasons = []
    if overlap > 0:
        reasons.append("overlap")
    if gap <= 5:
        reasons.append("gap<=5")
    if jaccard >= 0.45:
        reasons.append("hero_jaccard>=0.45")
    if len(shared) >= 2:
        reasons.append("shared>=2")
    if dist is not None and dist <= 2200:
        reasons.append("dist<=2200")
    if same_death:
        reasons.append("same_death")
    if one_no_kill:
        reasons.append("kill+no_kill")
    if both_no_kill:
        reasons.append("both_no_kill")

    suspicious = (
        (gap <= 5 or overlap > 0)
        and (jaccard >= 0.45 or len(shared) >= 2)
        and (dist is None or dist <= 2200)
        and (both_no_kill or same_death or one_no_kill)
    )
    return suspicious, {
        "left_id": left["id"],
        "left_time": left["time_range"],
        "left_labels": left["labels"],
        "left_result": left["结果"],
        "right_id": right["id"],
        "right_time": right["time_range"],
        "right_labels": right["labels"],
        "right_result": right["结果"],
        "gap": gap,
        "overlap": overlap,
        "hero_jaccard": round(jaccard, 3),
        "shared": shared,
        "distance": None if dist is None else round(dist),
        "reasons": reasons,
        "left_region": left.get("_region", ""),
        "right_region": right.get("_region", ""),
    }


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "v1.8"
    all_results = {}
    for match_id in MATCH_IDS:
        path = OUTPUTS / f"tactical_events_{match_id}" / f"tactical_events_{match_id}_event_table_clean_{version}.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        attach_positions(match_id, rows)
        combat_rows = [row for row in rows if has_combat_label(row)]
        candidates = []
        for left, right in zip(combat_rows, combat_rows[1:]):
            if parse_range(right["time_range"])[0] - parse_range(left["time_range"])[1] > 10 and overlap_seconds(left, right) == 0:
                continue
            suspicious, info = classify_pair(left, right)
            if suspicious:
                candidates.append(info)
        all_results[match_id] = candidates
    print(json.dumps(all_results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
