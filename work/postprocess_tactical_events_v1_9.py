import json
import re
import sys
from pathlib import Path

import postprocess_tactical_events_v1_8 as base


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
MATCH_IDS = ["8825993964", "8825996999", "8826042346", "8826052816", "8826099852"]


def time_to_seconds(value):
    text = str(value).strip()
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("-")
    minutes, seconds = text.split(":", 1)
    return sign * (int(minutes) * 60 + int(seconds))


def parse_range(value):
    text = str(value)
    match = re.match(r"(-?\d+:\d+)-(-?\d+:\d+)$", text)
    if not match:
        raise ValueError(f"Bad time_range: {value}")
    return time_to_seconds(match.group(1)), time_to_seconds(match.group(2))


def combat_rows_with_index(rows):
    return [(idx, row) for idx, row in enumerate(rows) if base.has_combat_label(row)]


def death_names(row):
    match = re.search(r"死亡：(.+)", str(row.get("结果", "")))
    if not match:
        return set()
    text = match.group(1).strip()
    if text == "无":
        return set()
    names = set()
    for item in re.split(r"[、,，]\s*", text):
        item = item.strip()
        m = re.search(r"\d+:\d+\s+(.+)$", item)
        names.add((m.group(1) if m else item).strip())
    return names


def has_substantive_kill(row):
    return bool(death_names(row)) or base.has_recorded_kill(row)


def no_independent_kill(row):
    return base.no_independent_kill(row)


def one_kill_one_no_kill(left, right):
    return (has_substantive_kill(left) and no_independent_kill(right)) or (
        has_substantive_kill(right) and no_independent_kill(left)
    )


def nearby_or_unknown(left, right, max_distance=2500):
    dist = base.distance(left.get("_pos"), right.get("_pos"))
    return dist is None or dist <= max_distance


def combat_gap_or_overlap(left, right, max_gap=5):
    left_range = parse_range(left["time_range"])
    right_range = parse_range(right["time_range"])
    if max(0, min(left_range[1], right_range[1]) - max(left_range[0], right_range[0])) > 0:
        return True
    return max(0, right_range[0] - left_range[1]) <= max_gap


def hero_overlap_ok(left, right):
    left_heroes = base.all_heroes(left)
    right_heroes = base.all_heroes(right)
    if not left_heroes or not right_heroes:
        return False
    shared = left_heroes & right_heroes
    jaccard = len(shared) / len(left_heroes | right_heroes)
    return len(shared) >= 2 or jaccard >= 0.33


def should_merge_kill_parent(left, right):
    if not (base.has_combat_label(left) and base.has_combat_label(right)):
        return False
    if not one_kill_one_no_kill(left, right):
        return False
    if not combat_gap_or_overlap(left, right):
        return False
    if not hero_overlap_ok(left, right):
        return False
    return nearby_or_unknown(left, right)


def merge_into_kill_parent(parent, child):
    parent_range = parse_range(parent["time_range"])
    child_range = parse_range(child["time_range"])
    merged = dict(parent)
    merged["time_range"] = base.format_range(min(parent_range[0], child_range[0]), max(parent_range[1], child_range[1]))
    labels = base.split_labels(parent.get("labels")) + base.split_labels(child.get("labels"))
    merged["labels"] = base.format_labels(labels)
    merged["confidence"] = max(float(parent.get("confidence") or 0), float(child.get("confidence") or 0))
    radiant = base.side_heroes(parent.get("heroes", ""), "天辉") | base.side_heroes(child.get("heroes", ""), "天辉")
    dire = base.side_heroes(parent.get("heroes", ""), "夜魇") | base.side_heroes(child.get("heroes", ""), "夜魇")
    merged["heroes"] = base.format_heroes(radiant, dire)
    merged["批注"] = ""
    merged["_pos"] = parent.get("_pos") or child.get("_pos")
    merged["_region"] = parent.get("_region") or child.get("_region")
    merged["_evidence"] = parent.get("_evidence") or child.get("_evidence")
    return merged


def merge_adjacent_kill_parent_combats(rows):
    rows = [dict(row) for row in rows]
    mapping = []
    changed = True
    while changed:
        changed = False
        combats = combat_rows_with_index(rows)
        for (left_idx, left), (right_idx, right) in zip(combats, combats[1:]):
            if not should_merge_kill_parent(left, right):
                continue
            if has_substantive_kill(left):
                parent_idx, child_idx = left_idx, right_idx
            else:
                parent_idx, child_idx = right_idx, left_idx
            parent = rows[parent_idx]
            child = rows[child_idx]
            merged = merge_into_kill_parent(parent, child)
            rows[parent_idx] = merged
            removed = rows.pop(child_idx)
            mapping.append({
                "parent_id": parent.get("id"),
                "child_id": removed.get("id"),
                "parent_time": parent.get("time_range"),
                "child_time": removed.get("time_range"),
                "new_time_range": merged["time_range"],
                "labels": merged["labels"],
                "result": merged["结果"],
                "reason": "有击杀父事件吸收相邻/重叠的无独立击杀短窗口",
            })
            changed = True
            break
    return rows, mapping


def sort_rows(rows):
    return sorted(rows, key=lambda row: (parse_range(row["time_range"])[0], parse_range(row["time_range"])[1], row.get("id", 0)))


def process_match(match_id):
    out_dir = OUTPUTS / f"tactical_events_{match_id}"
    source_path = out_dir / f"tactical_events_{match_id}_event_table_clean_v1.8.json"
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    by_time = base.load_supplements(match_id)
    base.attach_supplements(rows, by_time)

    merged_rows, kill_parent_mapping = merge_adjacent_kill_parent_combats(rows)
    merged_rows = sort_rows(merged_rows)
    output_rows = base.reassign_ids(merged_rows)

    stem = f"tactical_events_{match_id}_event_table_clean_v1.9"
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    mapping_path = out_dir / f"{stem}_mapping.json"

    json_path.write_text(json.dumps(output_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    base.write_csv(csv_path, output_rows)
    mapping_path.write_text(json.dumps({"kill_parent_merges": kill_parent_mapping}, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {}
    for row in output_rows:
        for label in base.split_labels(row.get("labels")):
            counts[label] = counts.get(label, 0) + 1

    md = [
        f"# {match_id} 战术事件表 v1.9",
        "",
        f"- v1.8 事件数: {len(rows)}",
        f"- 有击杀父事件吸收短窗口合并数: {len(kill_parent_mapping)}",
        f"- v1.9 输出事件数: {len(output_rows)}",
        "",
        "## 标签计数",
        "",
        "| 标签 | 数量 |",
        "| --- | ---: |",
    ]
    for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        md.append(f"| {label} | {count} |")
    md.extend(["", "## 合并记录", "", "| 父id | 子id | 父时间 | 子时间 | 新时间 | 标签 | 结果 |", "| ---: | ---: | --- | --- | --- | --- | --- |"])
    for item in kill_parent_mapping:
        md.append(
            f"| {item['parent_id']} | {item['child_id']} | {item['parent_time']} | {item['child_time']} | {item['new_time_range']} | {item['labels']} | {item['result']} |"
        )
    md.extend(["", "## 全部事件", "", "| " + " | ".join(base.FIELDNAMES) + " |", "| " + " | ".join(["---"] * len(base.FIELDNAMES)) + " |"])
    for row in output_rows:
        md.append("| " + " | ".join(base.clean_text(row.get(field)).replace("|", "/") for field in base.FIELDNAMES) + " |")
    md_path.write_text("\n".join(md), encoding="utf-8")

    return {
        "match_id": match_id,
        "source_rows": len(rows),
        "output_rows": len(output_rows),
        "kill_parent_merges": len(kill_parent_mapping),
        "mapping": kill_parent_mapping,
        "json_path": str(json_path),
        "mapping_path": str(mapping_path),
        "counts": counts,
    }


def main():
    match_ids = sys.argv[1:] or MATCH_IDS
    summaries = [process_match(match_id) for match_id in match_ids]
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
