import argparse
import csv
import json
import math
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

from pipeline_compute_fact_events import MatchContext, lane_from_xy, seconds_to_time, side_name, time_range, to_float, to_int


ROOT = Path(__file__).resolve().parents[1]
COMBAT_LABELS = {"单杀", "GANK", "小规模冲突", "团战"}
EVENT_FIELDNAMES = ["id", "match_id", "labels", "confidence", "time_range", "heroes", "结果", "evidence", "批注"]

PARAMS = {
    "damage_min_event": 45,
    "control_min_duration": 0.25,
    "cluster_gap_seconds": 12,
    "cluster_distance_game": 2200,
    "cluster_tail_seconds": 12,
    "cluster_tail_signal_count": 8,
    "merge_adjacent_gap_seconds": 3,
    "merge_adjacent_distance_game": 2200,
    "merge_adjacent_min_shared_heroes": 4,
    "merge_adjacent_min_jaccard": 0.5,
    "cluster_seed_damage": 550,
    "participant_near_radius_game": 1600,
    "remote_contribution_distance_game": 1400,
    "teamfight_min_side_count": 4,
    "teamfight_min_direct_count": 3,
    "teamfight_damage_threshold": 2500,
    "teamfight_death_threshold": 2,
    "teamfight_duration_threshold": 15,
    "skirmish_min_side_heroes": 2,
    "gank_min_attacker_side_heroes": 2,
    "gank_max_victim_side_heroes": 1,
    "gank_victim_damage_min": 600,
    "roshan_context_min_time": 600,
    "roshan_damage_context_pad": 30,
    "roshan_kill_context_before": 45,
    "roshan_kill_context_after": 75,
    "roshan_pit_context_radius_game": 2000,
    "tower_context_radius": 1000,
    "tower_context_time_pad": 5,
    "laning_kill_start_time": 20,
    "laning_kill_end_time": 300,
}

LANE_TEXT = {"top": "上路", "mid": "中路", "bot": "下路"}

ROSHAN_PITS = {
    "上路坑": {
        "server": (-3084, 2296),
        "raw": (102.587, 148.321),
    },
    "下路坑": {
        "server": (2842, -2743),
        "raw": (152.116, 105.919),
    },
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


def involves_illusion(row):
    return is_true(row.get("attackerillusion")) or is_true(row.get("targetillusion"))


def is_roshan_name(name):
    return str(name) == "npc_dota_roshan"


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
        if involves_illusion(row):
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
        attacker_pos = pos_index.nearest(attacker, t) if attacker else None
        target_pos = pos_index.nearest(target, t) if target else None
        signals.append({
            "time": t,
            "log_index": to_int(row.get("log_index"), 0) or 0,
            "kind": signal["kind"],
            "value": signal["value"],
            "attacker": attacker,
            "target": target,
            "pos": pos,
            "attacker_pos": attacker_pos,
            "target_pos": target_pos,
            "inflictor": row.get("inflictor") or "",
        })
    return sorted(signals, key=lambda item: (item["time"], item["log_index"]))


def can_attach(cluster, signal):
    if signal["time"] - cluster["end"] > PARAMS["cluster_gap_seconds"]:
        return False
    compare_center = cluster.get("tail_center") or cluster.get("center")
    if signal["pos"] is None or compare_center is None:
        return True
    return distance_game(compare_center, signal["pos"]) <= PARAMS["cluster_distance_game"]


def average_position(points):
    if not points:
        return None
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def cluster_tail_center(cluster):
    positioned = [item for item in cluster["signals"] if item.get("pos") is not None]
    if not positioned:
        return None
    tail_start = cluster["end"] - PARAMS["cluster_tail_seconds"]
    tail = [item for item in positioned if item["time"] >= tail_start]
    if len(tail) > PARAMS["cluster_tail_signal_count"]:
        tail = tail[-PARAMS["cluster_tail_signal_count"]:]
    if not tail:
        tail = positioned[-PARAMS["cluster_tail_signal_count"]:]
    return average_position([item["pos"] for item in tail])


def update_cluster(cluster, signal):
    cluster["signals"].append(signal)
    cluster["start"] = min(cluster["start"], signal["time"])
    cluster["end"] = max(cluster["end"], signal["time"])
    if signal["pos"] is not None:
        points = [item["pos"] for item in cluster["signals"] if item["pos"] is not None]
        cluster["center"] = average_position(points)
        cluster["tail_center"] = cluster_tail_center(cluster)


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
            "tail_center": signal["pos"],
            "signals": [signal],
        })
    return clusters


def cluster_stats(ctx, cluster):
    heroes = defaultdict(set)
    direct_heroes = defaultdict(set)
    damage_by_team = defaultdict(float)
    damage_taken_by_team = defaultdict(float)
    damage_taken_by_hero = defaultdict(float)
    remote_heroes = defaultdict(set)
    remote_damage_by_team = defaultdict(float)
    first_remote_time = {}
    first_direct_time = {}
    deaths = []
    seen_deaths = set()
    controls = []
    participants = set()

    for signal in cluster["signals"]:
        attacker = signal.get("attacker")
        target = signal.get("target")
        attacker_team = ctx.unit_team(attacker)
        target_team = ctx.unit_team(target)
        attacker_remote = False
        if attacker and target and signal["kind"] in {"damage", "control"}:
            attacker_dist = distance_game(signal.get("attacker_pos"), signal.get("target_pos"))
            attacker_remote = (
                attacker_dist is not None
                and attacker_dist > PARAMS["remote_contribution_distance_game"]
            )

        if target_team in {2, 3}:
            heroes[target_team].add(ctx.unit_hero(target))
            participants.add(target)

        if attacker_team in {2, 3}:
            if attacker_remote:
                hero = ctx.unit_hero(attacker)
                remote_heroes[attacker_team].add(hero)
                first_remote_time[(attacker_team, hero)] = min(
                    first_remote_time.get((attacker_team, hero), signal["time"]),
                    signal["time"],
                )
            else:
                hero = ctx.unit_hero(attacker)
                heroes[attacker_team].add(hero)
                participants.add(attacker)

        if attacker and signal["kind"] in {"damage", "control"} and not attacker_remote:
            team = attacker_team
            if team in {2, 3}:
                hero = ctx.unit_hero(attacker)
                direct_heroes[team].add(hero)
                first_direct_time[(team, hero)] = min(
                    first_direct_time.get((team, hero), signal["time"]),
                    signal["time"],
                )
        if signal["kind"] == "damage" and attacker:
            team = attacker_team
            if team in {2, 3}:
                if attacker_remote:
                    remote_damage_by_team[team] += signal["value"]
                else:
                    damage_by_team[team] += signal["value"]
                    if target_team in {2, 3}:
                        damage_taken_by_team[target_team] += signal["value"]
                        damage_taken_by_hero[ctx.unit_hero(target)] += signal["value"]
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
                "pos_raw": None if signal.get("pos") is None else [round(signal["pos"][0], 3), round(signal["pos"][1], 3)],
            })
        elif signal["kind"] == "control":
            controls.append(signal)

    remote_then_direct_radiant = {
        hero
        for hero in (remote_heroes[2] & direct_heroes[2])
        if first_remote_time.get((2, hero), 10**9) < first_direct_time.get((2, hero), -1)
    }
    remote_then_direct_dire = {
        hero
        for hero in (remote_heroes[3] & direct_heroes[3])
        if first_remote_time.get((3, hero), 10**9) < first_direct_time.get((3, hero), -1)
    }

    return {
        "radiant": sorted(heroes[2]),
        "dire": sorted(heroes[3]),
        "remote_radiant": sorted(remote_heroes[2] - heroes[2]),
        "remote_dire": sorted(remote_heroes[3] - heroes[3]),
        "remote_then_direct_radiant": sorted(remote_then_direct_radiant),
        "remote_then_direct_dire": sorted(remote_then_direct_dire),
        "direct_radiant": sorted(direct_heroes[2]),
        "direct_dire": sorted(direct_heroes[3]),
        "damage_radiant": round(damage_by_team[2]),
        "damage_dire": round(damage_by_team[3]),
        "damage_taken_radiant": round(damage_taken_by_team[2]),
        "damage_taken_dire": round(damage_taken_by_team[3]),
        "damage_taken_by_hero": {
            hero: round(value) for hero, value in sorted(damage_taken_by_hero.items())
        },
        "remote_damage_radiant": round(remote_damage_by_team[2]),
        "remote_damage_dire": round(remote_damage_by_team[3]),
        "deaths": deaths,
        "controls": controls,
        "signal_count": len(cluster["signals"]),
    }


def classify_fight(stats, duration):
    radiant_count = len(stats["radiant"])
    dire_count = len(stats["dire"])
    direct_radiant = len(stats["direct_radiant"])
    direct_dire = len(stats["direct_dire"])
    total_damage = stats["damage_radiant"] + stats["damage_dire"]
    death_count = len(stats["deaths"])
    radiant_death_count = sum(1 for item in stats["deaths"] if item["team"] == 2)
    dire_death_count = sum(1 for item in stats["deaths"] if item["team"] == 3)

    if radiant_count == 0 or dire_count == 0:
        return None
    if radiant_count == 1 and dire_count == 1:
        return "单杀" if death_count > 0 else None
    if (
        radiant_count >= PARAMS["gank_min_attacker_side_heroes"]
        and dire_count <= PARAMS["gank_max_victim_side_heroes"]
        and (
            stats["damage_taken_dire"] >= PARAMS["gank_victim_damage_min"]
            or dire_death_count > 0
        )
    ) or (
        dire_count >= PARAMS["gank_min_attacker_side_heroes"]
        and radiant_count <= PARAMS["gank_max_victim_side_heroes"]
        and (
            stats["damage_taken_radiant"] >= PARAMS["gank_victim_damage_min"]
            or radiant_death_count > 0
        )
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
    if radiant_count >= PARAMS["skirmish_min_side_heroes"] and dire_count >= PARAMS["skirmish_min_side_heroes"]:
        return "小规模冲突"
    return None


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
        f"damage_taken 天辉={stats.get('damage_taken_radiant', 0)} 夜魇={stats.get('damage_taken_dire', 0)} "
        f"by_hero={stats.get('damage_taken_by_hero', {})}; "
        f"remote_damage 天辉={stats.get('remote_damage_radiant', 0)} 夜魇={stats.get('remote_damage_dire', 0)}; "
        f"remote_heroes 天辉={stats.get('remote_radiant', [])} 夜魇={stats.get('remote_dire', [])}; "
        f"remote_then_direct 天辉={stats.get('remote_then_direct_radiant', [])} 夜魇={stats.get('remote_then_direct_dire', [])}; "
        f"center {center_text}; log_index {first['log_index']}-{last['log_index']}"
    )


def is_valid_seed(cluster, stats):
    total_damage = stats["damage_radiant"] + stats["damage_dire"]
    if total_damage >= PARAMS["cluster_seed_damage"]:
        return True
    if stats["deaths"]:
        return True
    return False


def add_context_label(label_text, context_label):
    labels = [label.strip() for label in str(label_text).split("/") if label.strip()]
    if context_label not in labels:
        labels.insert(0, context_label)
    return " / ".join(labels)


def label_set(label_text):
    return {label.strip() for label in str(label_text).split("/") if label.strip()}


def has_any_label(label_text, labels):
    return bool(label_set(label_text) & set(labels))


def record_heroes(record):
    return set(record.get("radiant", [])) | set(record.get("dire", []))


def record_center(record):
    center = record.get("center_raw")
    if not center:
        return None
    return tuple(center)


def should_merge_adjacent_records(left, right):
    gap = right["start"] - left["end"]
    if gap < 0 or gap > PARAMS["merge_adjacent_gap_seconds"]:
        return False
    if not (has_any_label(left["label"], {"团战"}) or has_any_label(right["label"], {"团战"})):
        return False

    left_heroes = record_heroes(left)
    right_heroes = record_heroes(right)
    shared = left_heroes & right_heroes
    union = left_heroes | right_heroes
    jaccard = len(shared) / len(union) if union else 0
    if len(shared) < PARAMS["merge_adjacent_min_shared_heroes"] and jaccard < PARAMS["merge_adjacent_min_jaccard"]:
        return False

    left_center = record_center(left)
    right_center = record_center(right)
    if left_center is not None and right_center is not None:
        if distance_game(left_center, right_center) > PARAMS["merge_adjacent_distance_game"]:
            return False
    return True


def merge_sorted_names(*groups):
    values = set()
    for group in groups:
        values.update(group or [])
    return sorted(values)


def merge_damage_by_hero(left, right):
    totals = defaultdict(float)
    for source in (left.get("damage_taken_by_hero", {}), right.get("damage_taken_by_hero", {})):
        for hero, value in source.items():
            totals[hero] += value
    return {hero: round(value) for hero, value in sorted(totals.items())}


def merge_adjacent_record(left, right):
    left_count = max(left.get("signal_count", 0), 1)
    right_count = max(right.get("signal_count", 0), 1)
    left_center = record_center(left)
    right_center = record_center(right)
    if left_center and right_center:
        center = (
            (left_center[0] * left_count + right_center[0] * right_count) / (left_count + right_count),
            (left_center[1] * left_count + right_center[1] * right_count) / (left_count + right_count),
        )
    else:
        center = left_center or right_center

    labels = []
    for label_text in (left["label"], right["label"]):
        for label in [item.strip() for item in str(label_text).split("/") if item.strip()]:
            if label not in labels:
                labels.append(label)
    if "团战" in labels:
        labels.remove("团战")
        labels.insert(0, "团战")

    deaths = sorted(left.get("deaths", []) + right.get("deaths", []), key=lambda item: (item.get("time", 0), item.get("hero", "")))
    merged = {
        **left,
        "end": max(left["end"], right["end"]),
        "time_range": time_range(left["start"], max(left["end"], right["end"])),
        "label": " / ".join(labels),
        "center_raw": None if center is None else [round(center[0], 3), round(center[1], 3)],
        "radiant": merge_sorted_names(left.get("radiant"), right.get("radiant")),
        "dire": merge_sorted_names(left.get("dire"), right.get("dire")),
        "remote_radiant": merge_sorted_names(left.get("remote_radiant"), right.get("remote_radiant")),
        "remote_dire": merge_sorted_names(left.get("remote_dire"), right.get("remote_dire")),
        "remote_then_direct_radiant": merge_sorted_names(left.get("remote_then_direct_radiant"), right.get("remote_then_direct_radiant")),
        "remote_then_direct_dire": merge_sorted_names(left.get("remote_then_direct_dire"), right.get("remote_then_direct_dire")),
        "direct_radiant": merge_sorted_names(left.get("direct_radiant"), right.get("direct_radiant")),
        "direct_dire": merge_sorted_names(left.get("direct_dire"), right.get("direct_dire")),
        "damage_radiant": left.get("damage_radiant", 0) + right.get("damage_radiant", 0),
        "damage_dire": left.get("damage_dire", 0) + right.get("damage_dire", 0),
        "damage_taken_radiant": left.get("damage_taken_radiant", 0) + right.get("damage_taken_radiant", 0),
        "damage_taken_dire": left.get("damage_taken_dire", 0) + right.get("damage_taken_dire", 0),
        "damage_taken_by_hero": merge_damage_by_hero(left, right),
        "remote_damage_radiant": left.get("remote_damage_radiant", 0) + right.get("remote_damage_radiant", 0),
        "remote_damage_dire": left.get("remote_damage_dire", 0) + right.get("remote_damage_dire", 0),
        "deaths": deaths,
        "signal_count": left.get("signal_count", 0) + right.get("signal_count", 0),
        "evidence": f"{left.get('evidence', '')}; merged_adjacent_fight gap={right['start'] - left['end']}s; next_evidence=({right.get('evidence', '')})",
    }
    return merged


def merge_adjacent_fight_records(records):
    merged = []
    for record in sorted(records, key=lambda item: (item["start"], item["end"])):
        if merged and should_merge_adjacent_records(merged[-1], record):
            merged[-1] = merge_adjacent_record(merged[-1], record)
        else:
            merged.append(record)
    return merged


def laning_kill_context_for_record(record):
    labels = label_set(record["label"])
    if not ({"单杀", "小规模冲突"} & labels):
        return None
    lane_deaths = defaultdict(list)
    evidence_parts = []
    for death in record.get("deaths", []):
        t = death.get("time")
        if t is None or t < PARAMS["laning_kill_start_time"] or t > PARAMS["laning_kill_end_time"]:
            continue
        pos = death.get("pos_raw")
        if not pos:
            continue
        lane = lane_from_xy(pos[0], pos[1])
        lane_name = LANE_TEXT.get(lane, lane)
        lane_deaths[lane_name].append(death)
        evidence_parts.append(
            f"laning_kill lane={lane_name} death_time={seconds_to_time(t)} "
            f"victim={death['hero']} killer={death.get('killer', '')} pos_raw=({pos[0]},{pos[1]})"
        )
    if not lane_deaths:
        return None

    parts = []
    for lane_name in sorted(lane_deaths):
        deaths = sorted(lane_deaths[lane_name], key=lambda item: item["time"])
        death_text = "、".join(f"{seconds_to_time(item['time'])} {item['hero']}" for item in deaths)
        parts.append(f"{lane_name} {len(deaths)} 次，{death_text}")
    return {
        "text": "；".join(parts),
        "evidence": "；".join(evidence_parts),
    }


def apply_laning_kill_context(records):
    annotated = []
    for record in records:
        context = laning_kill_context_for_record(record)
        if context:
            record = dict(record)
            record["label"] = add_context_label(record["label"], "对线击杀")
            record["laning_kill_context"] = context["text"]
            record["evidence"] = f"{record['evidence']}; {context['evidence']}"
        annotated.append(record)
    return annotated


def build_roshan_context(ctx):
    damage_times = []
    death_times = []
    chat_kill_times = []
    for row in ctx.combat:
        t = to_int(row.get("time"))
        if t is None:
            continue
        row_type = row.get("type")
        if row_type == "DOTA_COMBATLOG_DAMAGE" and (
            is_roshan_name(row.get("targetname")) or is_roshan_name(row.get("targetsourcename"))
        ):
            damage = to_float(row.get("value"), 0) or 0
            attacker = hero_from_log_name(ctx, row, "attackername", allow_source_fallback=True)
            if t >= PARAMS["roshan_context_min_time"] and damage > 0 and attacker:
                damage_times.append(t)
        elif row_type == "DOTA_COMBATLOG_DEATH" and (
            is_roshan_name(row.get("targetname")) or is_roshan_name(row.get("targetsourcename"))
        ):
            death_times.append(t)

    for row in ctx.chat:
        if row.get("type") == "CHAT_MESSAGE_ROSHAN_KILL":
            t = to_int(row.get("time"))
            if t is not None:
                chat_kill_times.append(t)

    return {
        "damage_times": sorted(damage_times),
        "kill_times": sorted(set(death_times + chat_kill_times)),
    }


def roshan_context_for_record(record, roshan_context):
    start = record["start"]
    end = record["end"]
    damage_matches = [
        t for t in roshan_context["damage_times"]
        if start - PARAMS["roshan_damage_context_pad"] <= t <= end + PARAMS["roshan_damage_context_pad"]
    ]
    kill_matches = [
        t for t in roshan_context["kill_times"]
        if start - PARAMS["roshan_kill_context_before"] <= t <= end + PARAMS["roshan_kill_context_after"]
    ]
    if damage_matches:
        return f"Roshan damage near fight at {seconds_to_time(damage_matches[0])}"
    if kill_matches:
        return f"Roshan kill near fight at {seconds_to_time(kill_matches[0])}"
    return ""


def apply_roshan_fight_context(records, roshan_context):
    annotated = []
    for record in records:
        context = roshan_context_for_record(record, roshan_context)
        if context and not has_any_label(record["label"], {"GANK", "单杀"}):
            record = dict(record)
            record["label"] = add_context_label(record["label"], "肉山团")
            record["evidence"] = f"{record['evidence']}; roshan_context={context}"
        annotated.append(record)
    return annotated


def roshan_pit_context_for_record(record):
    if not has_any_label(record["label"], {"团战", "小规模冲突"}):
        return None
    center = record.get("center_raw")
    if not center:
        return None
    best = None
    for pit_name, pit in ROSHAN_PITS.items():
        dist = distance_game(tuple(center), pit["raw"])
        if dist is None or dist > PARAMS["roshan_pit_context_radius_game"]:
            continue
        candidate = {
            "pit_name": pit_name,
            "distance_game": dist,
            "server": pit["server"],
            "raw": pit["raw"],
        }
        if best is None or candidate["distance_game"] < best["distance_game"]:
            best = candidate
    return best


def apply_roshan_pit_fight_context(records):
    annotated = []
    for record in records:
        context = roshan_pit_context_for_record(record)
        if context:
            record = dict(record)
            context_text = f"肉山{context['pit_name']}战斗"
            record["label"] = add_context_label(record["label"], "肉山团")
            record["roshan_pit_context"] = context_text
            record["evidence"] = (
                f"{record['evidence']}; roshan_pit_context={context_text} "
                f"server={context['server']} raw=({context['raw'][0]:.3f},{context['raw'][1]:.3f}) "
                f"distance_game={context['distance_game']:.0f} "
                f"radius_game={PARAMS['roshan_pit_context_radius_game']}"
            )
        annotated.append(record)
    return annotated


def build_tower_context(ctx):
    towers = defaultdict(list)
    for row in ctx.tables.get("tower_status_update", []):
        t = to_int(row.get("time"))
        team = to_int(row.get("team_num"))
        hp = to_float(row.get("hp"))
        max_hp = to_float(row.get("max_hp"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        ehandle = row.get("ehandle")
        if t is None or team not in (2, 3) or hp is None or x is None or y is None or not ehandle:
            continue
        towers[ehandle].append({
            "time": t,
            "team": team,
            "hp": hp,
            "max_hp": max_hp,
            "pos": (x, y),
        })
    for rows in towers.values():
        rows.sort(key=lambda item: item["time"])
    return towers


def tower_status_near_time(rows, time_value):
    if not rows:
        return None
    allowed_time = time_value + PARAMS["tower_context_time_pad"]
    latest = None
    for row in rows:
        if row["time"] <= allowed_time:
            latest = row
        else:
            break
    return latest


def tower_context_for_record(record, tower_context):
    center = record.get("center_raw")
    if not center:
        return None
    radius_raw = PARAMS["tower_context_radius"] / 130
    best = None
    for ehandle, rows in tower_context.items():
        status = tower_status_near_time(rows, record["start"])
        if not status or status["hp"] <= 0:
            continue
        dist = distance_raw(tuple(center), status["pos"])
        if dist is None or dist > radius_raw:
            continue
        candidate = {
            "ehandle": ehandle,
            "team": status["team"],
            "hp": status["hp"],
            "max_hp": status.get("max_hp"),
            "distance_raw": dist,
            "pos": status["pos"],
            "status_time": status["time"],
        }
        if best is None or candidate["distance_raw"] < best["distance_raw"]:
            best = candidate
    return best


def apply_tower_fight_context(records, tower_context):
    annotated = []
    for record in records:
        context = tower_context_for_record(record, tower_context)
        if context and not has_any_label(record["label"], {"GANK", "单杀"}):
            record = dict(record)
            side = side_name(context["team"])
            hp_text = int(round(context["hp"]))
            max_hp = context.get("max_hp")
            if max_hp:
                hp_text = f"{hp_text}/{int(round(max_hp))}"
            record["label"] = add_context_label(record["label"], "塔下")
            record["tower_context"] = f"{side}防御塔附近交战，塔血量{hp_text}"
            record["evidence"] = (
                f"{record['evidence']}; tower_context=ehandle={context['ehandle']} "
                f"team={side} hp={hp_text} distance_raw={context['distance_raw']:.1f} "
                f"status_time={seconds_to_time(context['status_time'])}"
            )
        annotated.append(record)
    return annotated


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
            "remote_radiant": stats["remote_radiant"],
            "remote_dire": stats["remote_dire"],
            "remote_then_direct_radiant": stats["remote_then_direct_radiant"],
            "remote_then_direct_dire": stats["remote_then_direct_dire"],
            "direct_radiant": stats["direct_radiant"],
            "direct_dire": stats["direct_dire"],
            "damage_radiant": stats["damage_radiant"],
            "damage_dire": stats["damage_dire"],
            "damage_taken_radiant": stats["damage_taken_radiant"],
            "damage_taken_dire": stats["damage_taken_dire"],
            "damage_taken_by_hero": stats["damage_taken_by_hero"],
            "remote_damage_radiant": stats["remote_damage_radiant"],
            "remote_damage_dire": stats["remote_damage_dire"],
            "deaths": stats["deaths"],
            "signal_count": stats["signal_count"],
            "evidence": evidence_text(cluster, stats),
        })
    records = merge_adjacent_fight_records(records)
    records = apply_roshan_fight_context(records, build_roshan_context(ctx))
    records = apply_roshan_pit_fight_context(records)
    records = apply_tower_fight_context(records, build_tower_context(ctx))
    records = apply_laning_kill_context(records)
    return records


def heroes_with_remote(normal, remote, remote_then_direct=None):
    names = list(normal or [])
    normal_set = set(names)
    remote_then_direct_set = set(remote_then_direct or [])
    names = [
        f"{hero}（远程后进场）" if hero in remote_then_direct_set else hero
        for hero in names
    ]
    for hero in remote or []:
        if hero not in normal_set:
            names.append(f"{hero}（远程）")
    return names


def records_to_events(ctx, records):
    rows = []
    for idx, record in enumerate(records, start=1):
        labels = {label.strip() for label in record["label"].split("/")}
        confidence = 0.74 if "团战" in labels else 0.7 if "GANK" in labels else 0.69 if "单杀" in labels else 0.68
        rows.append({
            "id": idx,
            "match_id": ctx.match_id,
            "labels": record["label"],
            "confidence": confidence,
            "time_range": record["time_range"],
            "heroes": ctx.heroes_text(
                heroes_with_remote(record["radiant"], record.get("remote_radiant"), record.get("remote_then_direct_radiant")),
                heroes_with_remote(record["dire"], record.get("remote_dire"), record.get("remote_then_direct_dire")),
            ),
            "结果": (
                result_text(record)
                + (f"；对线击杀：{record['laning_kill_context']}" if record.get("laning_kill_context") else "")
                + (f"；肉山坑上下文：{record['roshan_pit_context']}" if record.get("roshan_pit_context") else "")
                + (f"；守塔上下文：{record['tower_context']}" if record.get("tower_context") else "")
            ),
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
        for label in str(row["labels"]).split("/"):
            label = label.strip()
            if not label:
                continue
            counts[label] += 1
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
