import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
MATCH_IDS = ["8825993964", "8825996999", "8826042346", "8826052816", "8826099852"]
COMBAT_LABELS = {"小规模冲突", "团战", "肉山团", "高地团", "守塔团", "魔晶团"}
ROSHAN_DROP_KEYWORDS = ("获得肉山盾", "获得奶酪", "获得刷新碎片", "获得/放置战旗")
FIELDNAMES = ["id", "match_id", "labels", "confidence", "time_range", "heroes", "结果", "批注"]


def clean_text(value):
    return str(value or "").replace("\n", " ").strip()


def split_labels(value):
    return [label.strip() for label in re.split(r"\s+/\s+", str(value or "")) if label.strip()]


def format_labels(labels):
    return " / ".join(dict.fromkeys(label for label in labels if label))


def time_to_seconds(value):
    minutes, seconds = str(value).split(":", 1)
    return int(minutes) * 60 + int(seconds)


def seconds_to_time(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def parse_range(value):
    start, end = str(value).split("-", 1)
    return time_to_seconds(start), time_to_seconds(end)


def format_range(start, end):
    return f"{seconds_to_time(start)}-{seconds_to_time(end)}"


def has_combat_label(row):
    return bool(COMBAT_LABELS & set(split_labels(row.get("labels"))))


def has_recorded_kill(row):
    result = str(row.get("结果", ""))
    return "击杀结果：" in result and "未记录英雄击杀" not in result and "0 - 0" not in result


def no_independent_kill(row):
    result = str(row.get("结果", ""))
    return (
        "未记录英雄击杀" in result
        or "0 - 0" in result
        or result.strip() in {"", "击杀结果：未记录英雄击杀；死亡：无"}
    )


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


def format_heroes(radiant, dire):
    return f"天辉：{'、'.join(sorted(radiant)) if radiant else '无'}；夜魇：{'、'.join(sorted(dire)) if dire else '无'}"


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


def load_supplements(match_id):
    path = OUTPUTS / f"tactical_events_{match_id}" / f"tactical_events_{match_id}_v1.5.json"
    full_rows = json.loads(path.read_text(encoding="utf-8"))
    by_time = {}
    for row in full_rows:
        by_time.setdefault(row.get("time_range"), []).append(row)
    return by_time


def supplement_for(row, by_time):
    candidates = by_time.get(row.get("time_range"), [])
    if not candidates:
        return {}
    row_labels = set(split_labels(row.get("labels")))
    for candidate in candidates:
        candidate_labels = set(candidate.get("labels") or [])
        if row_labels & candidate_labels or ("抓人" in candidate_labels and row_labels & {"小规模冲突", "团战"}):
            return candidate
    return candidates[0]


def attach_supplements(rows, by_time):
    for row in rows:
        sup = supplement_for(row, by_time)
        row["_region"] = sup.get("region", "")
        row["_evidence"] = sup.get("evidence", "")
        row["_pos"] = parse_game_position(" ".join([str(sup.get("region", "")), str(sup.get("evidence", ""))]))


def is_roshan_kill_row(row):
    return "资源获得" in split_labels(row.get("labels")) and "击杀肉山" in str(row.get("结果", ""))


def is_roshan_drop_row(row):
    result = str(row.get("结果", ""))
    return "资源获得" in split_labels(row.get("labels")) and any(keyword in result for keyword in ROSHAN_DROP_KEYWORDS)


def merge_hero_text(rows):
    radiant = set()
    dire = set()
    for row in rows:
        radiant |= side_heroes(row.get("heroes", ""), "天辉")
        dire |= side_heroes(row.get("heroes", ""), "夜魇")
    return format_heroes(radiant, dire)


def merge_roshan_kill_events(rows):
    output = []
    used = set()
    mapping = []
    for idx, row in enumerate(rows):
        if idx in used:
            continue
        if not is_roshan_kill_row(row):
            output.append(row)
            continue

        start, end = parse_range(row["time_range"])
        related = []
        for j in range(idx + 1, len(rows)):
            other = rows[j]
            o_start, o_end = parse_range(other["time_range"])
            if o_start - start > 20:
                break
            if is_roshan_drop_row(other) and 0 <= o_start - start <= 20:
                related.append((j, other))
                end = max(end, o_end)

        new_row = dict(row)
        new_row["labels"] = "肉山击杀"
        new_row["time_range"] = format_range(start, end)
        merged_rows = [row] + [item for _j, item in related]
        new_row["heroes"] = merge_hero_text(merged_rows)
        pieces = [str(row.get("结果", "")).strip()]
        pieces.extend(str(item.get("结果", "")).strip() for _j, item in related if str(item.get("结果", "")).strip())
        new_row["结果"] = "；".join(dict.fromkeys(pieces))
        new_row["批注"] = new_row.get("批注", "")
        output.append(new_row)

        for j, _other in related:
            used.add(j)
        if related:
            mapping.append({
                "roshan_kill_time": row["time_range"],
                "new_time_range": new_row["time_range"],
                "new_result": new_row["结果"],
                "merged_drop_ids": [other["id"] for _j, other in related],
            })
    return output, mapping


def combat_duplicate(left, right):
    if not (has_combat_label(left) and has_combat_label(right)):
        return False
    if has_recorded_kill(left) or has_recorded_kill(right):
        return False
    if not (no_independent_kill(left) and no_independent_kill(right)):
        return False

    l_range = parse_range(left["time_range"])
    r_range = parse_range(right["time_range"])
    if r_range[0] > l_range[1] + 3:
        return False
    if l_range[0] > r_range[1] + 3:
        return False

    l_heroes = all_heroes(left)
    r_heroes = all_heroes(right)
    if not l_heroes or not r_heroes:
        return False
    intersection = l_heroes & r_heroes
    union = l_heroes | r_heroes
    jaccard = len(intersection) / len(union)
    if jaccard < 0.50 and len(intersection) < 2:
        return False

    dist = distance(left.get("_pos"), right.get("_pos"))
    if dist is not None and dist > 1800:
        return False

    label_overlap = set(split_labels(left.get("labels"))) & set(split_labels(right.get("labels"))) & COMBAT_LABELS
    if not label_overlap:
        return False
    return True


def merge_combat_group(group):
    start = min(parse_range(row["time_range"])[0] for row in group)
    end = max(parse_range(row["time_range"])[1] for row in group)
    base = dict(group[0])
    labels = []
    for row in group:
        labels.extend(split_labels(row.get("labels")))
    base["labels"] = format_labels(labels)
    base["time_range"] = format_range(start, end)
    base["confidence"] = max(float(row.get("confidence") or 0) for row in group)
    base["heroes"] = merge_hero_text(group)
    base["结果"] = "击杀结果：未记录英雄击杀；死亡：无"
    base["批注"] = ""
    return base


def merge_adjacent_duplicate_combats(rows):
    output = []
    mapping = []
    i = 0
    while i < len(rows):
        row = rows[i]
        if not has_combat_label(row) or has_recorded_kill(row):
            output.append(row)
            i += 1
            continue

        group = [row]
        j = i + 1
        while j < len(rows) and combat_duplicate(group[-1], rows[j]):
            group.append(rows[j])
            j += 1

        if len(group) > 1:
            merged = merge_combat_group(group)
            output.append(merged)
            mapping.append({
                "merged_ids": [item["id"] for item in group],
                "old_time_ranges": [item["time_range"] for item in group],
                "new_time_range": merged["time_range"],
                "labels": merged["labels"],
                "heroes": merged["heroes"],
            })
        else:
            output.append(row)
        i = j
    return output, mapping


def strip_internal(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def reassign_ids(rows):
    clean_rows = []
    for idx, row in enumerate(rows, start=1):
        clean = strip_internal(row)
        clean["id"] = idx
        clean["批注"] = clean.get("批注", "")
        clean_rows.append(clean)
    return clean_rows


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def process_match(match_id):
    out_dir = OUTPUTS / f"tactical_events_{match_id}"
    source_path = out_dir / f"tactical_events_{match_id}_event_table_clean_v1.7.json"
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    by_time = load_supplements(match_id)
    attach_supplements(rows, by_time)

    roshan_rows, roshan_mapping = merge_roshan_kill_events(rows)
    attach_supplements(roshan_rows, by_time)
    combat_rows, combat_mapping = merge_adjacent_duplicate_combats(roshan_rows)
    output_rows = reassign_ids(combat_rows)

    stem = f"tactical_events_{match_id}_event_table_clean_v1.8"
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    mapping_path = out_dir / f"{stem}_mapping.json"

    json_path.write_text(json.dumps(output_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, output_rows)
    mapping_path.write_text(
        json.dumps({"roshan_kill_merges": roshan_mapping, "combat_duplicate_merges": combat_mapping}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counts = Counter(label for row in output_rows for label in split_labels(row.get("labels")))
    md = [
        f"# {match_id} 战术事件表 v1.8",
        "",
        f"- v1.7 事件数: {len(rows)}",
        f"- 肉山掉落合并数: {sum(len(item['merged_drop_ids']) for item in roshan_mapping)}",
        f"- 相邻近距战斗重复合并组数: {len(combat_mapping)}",
        f"- v1.8 输出事件数: {len(output_rows)}",
        "",
        "## 标签计数",
        "",
        "| 标签 | 数量 |",
        "| --- | ---: |",
    ]
    for label, count in counts.most_common():
        md.append(f"| {label} | {count} |")
    md.extend(["", "## 肉山击杀合并", "", "| 时间 | 新时间 | 结果 | 合并掉落原id |", "| --- | --- | --- | --- |"])
    for item in roshan_mapping:
        md.append(f"| {item['roshan_kill_time']} | {item['new_time_range']} | {item['new_result']} | {', '.join(map(str, item['merged_drop_ids']))} |")
    md.extend(["", "## 相邻战斗重复合并", "", "| 原id | 原时间 | 新时间 | 标签 | heroes |", "| --- | --- | --- | --- | --- |"])
    for item in combat_mapping:
        md.append(
            f"| {', '.join(map(str, item['merged_ids']))} | {', '.join(item['old_time_ranges'])} | {item['new_time_range']} | {item['labels']} | {item['heroes']} |"
        )
    md.extend(["", "## 全部事件", "", "| " + " | ".join(FIELDNAMES) + " |", "| " + " | ".join(["---"] * len(FIELDNAMES)) + " |"])
    for row in output_rows:
        md.append("| " + " | ".join(clean_text(row.get(field)).replace("|", "/") for field in FIELDNAMES) + " |")
    md_path.write_text("\n".join(md), encoding="utf-8")

    return {
        "match_id": match_id,
        "source_rows": len(rows),
        "output_rows": len(output_rows),
        "roshan_kill_merges": len(roshan_mapping),
        "roshan_drop_rows_merged": sum(len(item["merged_drop_ids"]) for item in roshan_mapping),
        "combat_duplicate_merges": len(combat_mapping),
        "counts": dict(counts),
        "json_path": str(json_path),
        "mapping_path": str(mapping_path),
        "combat_mapping": combat_mapping,
        "roshan_mapping": roshan_mapping,
    }


def main():
    match_ids = sys.argv[1:] or MATCH_IDS
    summaries = [process_match(match_id) for match_id in match_ids]
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
