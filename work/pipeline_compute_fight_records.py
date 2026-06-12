import argparse
import csv
import json
import math
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

from pipeline_compute_fact_events import MatchContext, seconds_to_time, time_range, to_float, to_int


ROOT = Path(__file__).resolve().parents[1]
COMBAT_LABELS = {"GANK", "小规模冲突", "团战"}
EVENT_FIELDNAMES = ["id", "match_id", "labels", "confidence", "time_range", "heroes", "结果", "evidence", "批注"]

PARAMS = {
    "damage_min_event": 45,
    "control_min_duration": 0.25,
    "cluster_gap_seconds": 12,
    "cluster_distance_raw": 48,
    "cluster_seed_damage": 550,
    "cluster_min_signals": 2,
    "participant_near_radius_game": 1600,
    "teamfight_min_side_count": 4,
    "teamfight_min_direct_count": 3,
    "teamfight_damage_threshold": 2500,
    "teamfight_death_threshold": 2,
    "teamfight_duration_threshold": 15,
    "skirmish_min_total_heroes": 2,
    "gank_min_attacker_side_heroes": 2,
    "gank_max_victim_side_heroes": 1,
}


def raw_to_game(value):
    return value * 130


def distance_raw(a, b):
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def distance_game(a, b):
    if a is None or b is None:
        return None
    return math.hypot(raw_to_game(a[0]) - raw_to_game(b[0]), raw_to_game(a[1]) - raw_to_game(b[1]))


def is_true(value):
    return str(value).lower() == "true"


def hero_from_log_name(ctx, row, key, allow_source_fallback=True):
    value = row.get(key)
    if value and value in ctx.unit_to_cn:
        return value
    if not allow_source_fallback:
        return None
    source_key = "sourcename" if key == "attackername" else "targetsourcename"
    value = row.get(source_key)
    if value and value in ctx.unit_to_cn:
        return value
    return None


class HeroPositionIndex:
    def __init__(self, ctx):
        self.by_unit = defaultdict(list)
        slot_to_unit = {to_int(row.get("slot")): row.get("hero_name") for row in ctx.players}
        for row in ctx.intervals:
            slot = to_int(row.get("slot"))
            unit = slot_to_unit.get(slot)
            if not unit:
                continue
            x = to_float(row.get("x"))
            y = to_float(row.get("y"))
            t = to_int(row.get("time"))
            if x is None or y is None or t is None:
                continue
            self.by_unit[unit].append((t, (x, y)))
        for rows in self.by_unit.values():
            rows.sort(key=lambda item: item[0])

    def nearest(self, unit, time, max_gap=6):
        rows = self.by_unit.get(unit) or []
        if not rows:
            return None
        idx = bisect_left([item[0] for item in rows], time)
        candidates = []
        if idx < len(rows):
            candidates.append(rows[idx])
        if idx > 0:
            candidates.append(rows[idx - 1])
        if not candidates:
            return None
        best = min(candidates, key=lambda item: abs(item[0] - time))
        if abs(best[0] - time) > max_gap:
            return None
        return best[1]


def row_position(row, pos_index, attacker, target):
    x = to_float(row.get("x"))
    y = to_float(row.get("y"))
    if x is not None and y is not None:
        return (x, y)
    t = to_int(row.get("time"), 0)
    return pos_index.nearest(target, t) or pos_index.nearest(attacker, t)


def control_duration(row):
    durations = [
        to_float(row.get("stun_duration"), 0) or 0,
        to_float(row.get("slow_duration"), 0) or 0,
    ]
    flags = ["silence_modifier", "root_modifier", "motion_controller_modifier"]
    if any(is_true(row.get(flag)) for flag in flags):
        durations.append(PARAMS["control_min_duration"])
    return max(durations)


def build_signals(ctx):
    pos_index = HeroPositionIndex(ctx)
    signals = []
    for row in ctx.combat:
        t = to_int(row.get("time"))
        if t is None:
            continue
        row_type = row.get("type")
        attacker = hero_from_log_name(ctx, row, "attackername", allow_source_fallback=True)
        target = hero_from_log_name(
            ctx,
            row,
            "targetname",
            allow_source_fallback=row_type != "DOTA_COMBATLOG_DEATH" and is_true(row.get("targethero")),
        )
        if not attacker and not target:
            continue

        signal = None
        if row_type == "DOTA_COMBATLOG_DAMAGE" and attacker and target and attacker != target:
            value = to_float(row.get("value"), 0) or 0
            if value >= PARAMS["damage_min_event"]:
                signal = {"kind": "damage", "value": value}
        elif row_type == "DOTA_COMBATLOG_DEATH" and target and is_true(row.get("targethero")):
            signal = {"kind": "death", "value": 1}
        elif row_type == "DOTA_COMBATLOG_MODIFIER_ADD" and attacker and target and attacker != target and is_true(row.get("targethero")):
            duration = control_duration(row)
            if duration >= PARAMS["control_min_duration"]:
                signal = {"kind": "control", "value": duration}

        if not signal:
            continue

        pos = row_position(row, pos_index, attacker, target)
        if pos is None and signal["kind"] != "death":
            continue
        signals.append({
            "time": t,
            "log_index": to_int(row.get("log_index"), 0) or 0,
            "kind": signal["kind"],
            "value": signal["value"],
            "attacker": attacker,
            "target": target,
            "pos": pos,
            "inflictor": row.get("inflictor") or "",
        })
    return sorted(signals, key=lambda item: (item["time"], item["log_index"]))


def can_attach(cluster, signal):
    if signal["time"] - cluster["end"] > PARAMS["cluster_gap_seconds"]:
        return False
    if signal["pos"] is None or cluster["center"] is None:
        return True
    return distance_raw(cluster["center"], signal["pos"]) <= PARAMS["cluster_distance_raw"]


def update_cluster(cluster, signal):
    cluster["signals"].append(signal)
    cluster["start"] = min(cluster["start"], signal["time"])
    cluster["end"] = max(cluster["end"], signal["time"])
    if signal["pos"] is not None:
        points = [item["pos"] for item in cluster["signals"] if item["pos"] is not None]
        cluster["center"] = (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )


def cluster_signals(signals):
    clusters = []
    for signal in signals:
        attached = None
        for cluster in reversed(clusters[-8:]):
            if can_attach(cluster, signal):
                attached = cluster
                break
        if attached:
            update_cluster(attached, signal)
            continue
        clusters.append({
            "start": signal["time"],
            "end": signal["time"],
            "center": signal["pos"],
            "signals": [signal],
        })
    return clusters


def cluster_stats(ctx, cluster):
    heroes = defaultdict(set)
    direct_heroes = defaultdict(set)
    damage_by_team = defaultdict(float)
    deaths = []
    seen_deaths = set()
    controls = []
    participants = set()

    for signal in cluster["signals"]:
        attacker = signal.get("attacker")
        target = signal.get("target")
        for unit in (attacker, target):
            team = ctx.unit_team(unit)
            if team in {2, 3}:
                heroes[team].add(ctx.unit_hero(unit))
                participants.add(unit)
        if attacker and signal["kind"] in {"damage", "control"}:
            team = ctx.unit_team(attacker)
            if team in {2, 3}:
                direct_heroes[team].add(ctx.unit_hero(attacker))
        if signal["kind"] == "damage" and attacker:
            team = ctx.unit_team(attacker)
            if team in {2, 3}:
                damage_by_team[team] += signal["value"]
        elif signal["kind"] == "death" and target:
            death_key = (signal["time"], target)
            if death_key in seen_deaths:
                continue
            seen_deaths.add(death_key)
            deaths.append({
                "time": signal["time"],
                "hero": ctx.unit_hero(target),
                "team": ctx.unit_team(target),
                "killer": ctx.unit_hero(attacker) if attacker else "",
            })
        elif signal["kind"] == "control":
            controls.append(signal)

    return {
        "radiant": sorted(heroes[2]),
        "dire": sorted(heroes[3]),
        "direct_radiant": sorted(direct_heroes[2]),
        "direct_dire": sorted(direct_heroes[3]),
        "damage_radiant": round(damage_by_team[2]),
        "damage_dire": round(damage_by_team[3]),
        "deaths": deaths,
        "controls": controls,
        "signal_count": len(cluster["signals"]),
    }


def classify_fight(stats, duration):
    radiant_count = len(stats["radiant"])
    dire_count = len(stats["dire"])
    total = radiant_count + dire_count
    direct_radiant = len(stats["direct_radiant"])
    direct_dire = len(stats["direct_dire"])
    total_damage = stats["damage_radiant"] + stats["damage_dire"]
    death_count = len(stats["deaths"])

    if radiant_count == 0 or dire_count == 0:
        return None
    if total < PARAMS["skirmish_min_total_heroes"] and death_count == 0:
        return None
    if (
        radiant_count >= PARAMS["gank_min_attacker_side_heroes"]
        and dire_count <= PARAMS["gank_max_victim_side_heroes"]
    ) or (
        dire_count >= PARAMS["gank_min_attacker_side_heroes"]
        and radiant_count <= PARAMS["gank_max_victim_side_heroes"]
    ):
        return "GANK"
    if (
        radiant_count >= PARAMS["teamfight_min_side_count"]
        and dire_count >= PARAMS["teamfight_min_side_count"]
        and direct_radiant >= PARAMS["teamfight_min_direct_count"]
        and direct_dire >= PARAMS["teamfight_min_direct_count"]
        and (
            total_damage >= PARAMS["teamfight_damage_threshold"]
            or death_count >= PARAMS["teamfight_death_threshold"]
            or duration >= PARAMS["teamfight_duration_threshold"]
        )
    ):
        return "团战"
    return "小规模冲突"


def result_text(stats):
    radiant_kills = sum(1 for item in stats["deaths"] if item["team"] == 3)
    dire_kills = sum(1 for item in stats["deaths"] if item["team"] == 2)
    if stats["deaths"]:
        death_text = "、".join(f"{seconds_to_time(item['time'])} {item['hero']}" for item in stats["deaths"])
    else:
        death_text = "无"
    return f"击杀结果：天辉 {radiant_kills} - {dire_kills} 夜魇；死亡：{death_text}"


def evidence_text(cluster, stats):
    center = cluster["center"]
    center_text = "unknown" if center is None else f"raw=({center[0]:.1f},{center[1]:.1f}); game=({raw_to_game(center[0]):.0f},{raw_to_game(center[1]):.0f})"
    kinds = defaultdict(int)
    for signal in cluster["signals"]:
        kinds[signal["kind"]] += 1
    first = cluster["signals"][0]
    last = cluster["signals"][-1]
    return (
        f"signals={dict(kinds)}; damage 天辉={stats['damage_radiant']} 夜魇={stats['damage_dire']}; "
        f"center {center_text}; log_index {first['log_index']}-{last['log_index']}"
    )


def is_valid_seed(cluster, stats):
    total_damage = stats["damage_radiant"] + stats["damage_dire"]
    if total_damage >= PARAMS["cluster_seed_damage"]:
        return True
    if stats["deaths"]:
        return True
    return stats["signal_count"] >= PARAMS["cluster_min_signals"] and stats["controls"]


def build_fight_records(ctx):
    signals = build_signals(ctx)
    raw_clusters = cluster_signals(signals)
    records = []
    for cluster in raw_clusters:
        stats = cluster_stats(ctx, cluster)
        duration = cluster["end"] - cluster["start"]
        label = classify_fight(stats, duration)
        if not label or not is_valid_seed(cluster, stats):
            continue
        records.append({
            "match_id": ctx.match_id,
            "start": cluster["start"],
            "end": cluster["end"],
            "time_range": time_range(cluster["start"], cluster["end"]),
            "label": label,
            "center_raw": None if cluster["center"] is None else [round(cluster["center"][0], 3), round(cluster["center"][1], 3)],
            "radiant": stats["radiant"],
            "dire": stats["dire"],
            "direct_radiant": stats["direct_radiant"],
            "direct_dire": stats["direct_dire"],
            "damage_radiant": stats["damage_radiant"],
            "damage_dire": stats["damage_dire"],
            "deaths": stats["deaths"],
            "signal_count": stats["signal_count"],
            "evidence": evidence_text(cluster, stats),
        })
    return records


def records_to_events(ctx, records):
    rows = []
    for idx, record in enumerate(records, start=1):
        confidence = 0.74 if record["label"] == "团战" else 0.7 if record["label"] == "GANK" else 0.68
        rows.append({
            "id": idx,
            "match_id": ctx.match_id,
            "labels": record["label"],
            "confidence": confidence,
            "time_range": record["time_range"],
            "heroes": ctx.heroes_text(record["radiant"], record["dire"]),
            "结果": result_text(record),
            "evidence": record["evidence"],
            "批注": "",
        })
    return rows


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EVENT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def compute(match_dir, output_dir):
    ctx = MatchContext(match_dir)
    records = build_fight_records(ctx)
    events = records_to_events(ctx, records)

    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / f"fight_records_{ctx.match_id}.json"
    events_path = output_dir / f"fight_events_{ctx.match_id}.json"
    csv_path = output_dir / f"fight_events_{ctx.match_id}.csv"
    records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    events_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, events)

    counts = defaultdict(int)
    for row in events:
        counts[row["labels"]] += 1
    return {
        "match_id": ctx.match_id,
        "fight_records": len(records),
        "event_counts": dict(sorted(counts.items())),
        "records": str(records_path),
        "events": str(events_path),
        "csv": str(csv_path),
        "params": PARAMS,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Build fight_records and first-pass combat tactical events.")
    parser.add_argument("match_dir", help="Directory produced by pipeline_extract_match.py.")
    parser.add_argument("--out", default=str(ROOT / "computed_events"), help="Output directory.")
    return parser.parse_args()


def main():
    args = parse_args()
    summary = compute(Path(args.match_dir), Path(args.out))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
