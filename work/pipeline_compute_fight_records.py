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
    "damage_min_event_pre10": 30,
    "damage_min_event_post10": 45,
    "control_min_duration": 0.25,
    "cluster_gap_seconds_pre10": 7,
    "cluster_gap_seconds_post10": 12,
    "cluster_distance_game": 2200,
    "cluster_tail_seconds_pre10": 7,
    "cluster_tail_seconds_post10": 12,
    "cluster_tail_signal_count": 8,
    "merge_adjacent_gap_seconds": 3,
    "merge_adjacent_distance_game": 2200,
    "merge_adjacent_min_shared_heroes": 4,
    "merge_adjacent_min_jaccard": 0.5,
    "merge_hero_center_distance_game": 2500,
    "merge_hero_center_min_shared_heroes": 2,
    "merge_hero_center_min_jaccard": 0.2,
    "merge_chase_gap_seconds": 10,
    "merge_chase_center_distance_game": 3500,
    "merge_chase_min_shared_attackers": 2,
    "merge_interaction_continuation_gap_seconds_pre10": 6,
    "merge_interaction_continuation_gap_seconds_post10": 13,
    "merge_interaction_continuation_step_distance_game": 3500,
    "merge_interaction_continuation_position_pad_seconds": 13,
    "cluster_seed_damage": 550,
    "participant_near_radius_game": 1600,
    "remote_contribution_distance_game": 2000,
    "teamfight_min_side_count": 4,
    "teamfight_min_direct_count": 3,
    "teamfight_damage_threshold": 2500,
    "teamfight_death_threshold": 2,
    "teamfight_duration_threshold": 15,
    "skirmish_min_side_heroes": 2,
    "gank_min_attacker_side_heroes": 2,
    "gank_max_victim_side_heroes": 1,
    "gank_victim_damage_min": 600,
    "roshan_context_min_time": 900,
    "roshan_damage_context_pad": 30,
    "roshan_kill_context_before": 45,
    "roshan_kill_context_after": 75,
    "roshan_pit_context_radius_game": 1000,
    "roshan_pit_opening_seconds": 3,
    "skirmish_pre10_single_hero_damage_threshold": 400,
    "skirmish_pre10_total_damage_threshold": 900,
    "skirmish_pre10_short_duration_seconds": 12,
    "skirmish_pre10_burst_window_seconds": 8,
    "skirmish_pre10_burst_total_damage_threshold": 700,
    "skirmish_pre10_burst_single_hero_damage_threshold": 360,
    "tower_context_radius": 1000,
    "tower_context_time_pad": 5,
    "tower_context_opening_seconds": 3,
    "laning_kill_start_time": 20,
    "laning_kill_end_time": 300,
}

LANE_TEXT = {"top": "上路", "mid": "中路", "bot": "下路"}

ROSHAN_PITS = {
    "上路坑": {
        "server": (-2924.964844, 1522.657715),
        "raw": (104.015, 141.712),
    },
    "下路坑": {
        "server": (2867.646973, -3401.379395),
        "raw": (152.291, 100.499),
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


CONTROLLED_UNIT_OWNER_PREFIXES = [
    ("npc_dota_lone_druid_bear", "npc_dota_hero_lone_druid"),
    ("npc_dota_furion_treant", "npc_dota_hero_furion"),
    ("npc_dota_lycan_wolf", "npc_dota_hero_lycan"),
    ("npc_dota_beastmaster_boar", "npc_dota_hero_beastmaster"),
    ("npc_dota_beastmaster_hawk", "npc_dota_hero_beastmaster"),
    ("npc_dota_broodmother_spider", "npc_dota_hero_broodmother"),
    ("npc_dota_warlock_golem", "npc_dota_hero_warlock"),
    ("npc_dota_venomancer_plague_ward", "npc_dota_hero_venomancer"),
    ("npc_dota_shadow_shaman_ward", "npc_dota_hero_shadow_shaman"),
    ("npc_dota_witch_doctor_death_ward", "npc_dota_hero_witch_doctor"),
]


def controlled_unit_owner(ctx, value):
    text = str(value or "")
    for prefix, owner in CONTROLLED_UNIT_OWNER_PREFIXES:
        if text.startswith(prefix) and owner in ctx.unit_to_cn:
            return owner
    return None


def hero_from_log_name(ctx, row, key, allow_source_fallback=True):
    value = row.get(key)
    if value and value in ctx.unit_to_cn:
        return value
    owner = controlled_unit_owner(ctx, value)
    if owner:
        return owner
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


def is_pre10_time(time_value):
    return time_value is not None and time_value < 600


def damage_min_event_for_time(time_value):
    if is_pre10_time(time_value):
        return PARAMS["damage_min_event_pre10"]
    return PARAMS["damage_min_event_post10"]


def cluster_gap_seconds_for_time(time_value):
    if is_pre10_time(time_value):
        return PARAMS["cluster_gap_seconds_pre10"]
    return PARAMS["cluster_gap_seconds_post10"]


def cluster_tail_seconds_for_time(time_value):
    if is_pre10_time(time_value):
        return PARAMS["cluster_tail_seconds_pre10"]
    return PARAMS["cluster_tail_seconds_post10"]


def interaction_continuation_gap_seconds_for_time(time_value):
    if is_pre10_time(time_value):
        return PARAMS["merge_interaction_continuation_gap_seconds_pre10"]
    return PARAMS["merge_interaction_continuation_gap_seconds_post10"]


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
            if value >= damage_min_event_for_time(t):
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
    if signal["time"] - cluster["end"] > cluster_gap_seconds_for_time(signal["time"]):
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
    tail_start = cluster["end"] - cluster_tail_seconds_for_time(cluster["end"])
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
    if death_count > 0 and (
        (radiant_death_count > 0 and direct_dire == 1)
        or (dire_death_count > 0 and direct_radiant == 1)
    ):
        return "单杀"
    if direct_radiant == 1 and direct_dire == 1 and death_count > 0:
        return "单杀"

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


def death_window_core_stats(ctx, cluster, stats):
    if not stats.get("deaths"):
        return stats
    window_seconds = 12
    radiant = set()
    dire = set()
    direct_radiant = set()
    direct_dire = set()
    death_times = [item["time"] for item in stats.get("deaths", [])]
    for death in stats.get("deaths", []):
        team = death.get("team")
        hero = death.get("hero")
        if team == 2:
            radiant.add(hero)
        elif team == 3:
            dire.add(hero)
    for signal in cluster.get("signals", []):
        if signal.get("kind") not in {"damage", "control", "death"}:
            continue
        if not any(death_time - window_seconds <= signal["time"] <= death_time for death_time in death_times):
            continue
        attacker = signal.get("attacker")
        target = signal.get("target")
        attacker_team = ctx.unit_team(attacker)
        target_team = ctx.unit_team(target)
        attacker_remote = False
        if attacker and target and signal.get("kind") in {"damage", "control"}:
            attacker_dist = distance_game(signal.get("attacker_pos"), signal.get("target_pos"))
            attacker_remote = (
                attacker_dist is not None
                and attacker_dist > PARAMS["remote_contribution_distance_game"]
            )
        if target_team == 2:
            radiant.add(ctx.unit_hero(target))
        elif target_team == 3:
            dire.add(ctx.unit_hero(target))
        if attacker_team == 2 and not attacker_remote:
            hero = ctx.unit_hero(attacker)
            radiant.add(hero)
            direct_radiant.add(hero)
        elif attacker_team == 3 and not attacker_remote:
            hero = ctx.unit_hero(attacker)
            dire.add(hero)
            direct_dire.add(hero)
    adjusted = dict(stats)
    adjusted["radiant"] = sorted(radiant) or stats.get("radiant", [])
    adjusted["dire"] = sorted(dire) or stats.get("dire", [])
    adjusted["direct_radiant"] = sorted(direct_radiant) or stats.get("direct_radiant", [])
    adjusted["direct_dire"] = sorted(direct_dire) or stats.get("direct_dire", [])
    return adjusted


def prune_death_record_participants(record):
    if not record.get("deaths"):
        return record
    direct_radiant = set(record.get("direct_radiant") or [])
    direct_dire = set(record.get("direct_dire") or [])
    if not direct_radiant or not direct_dire:
        return record
    pruned = dict(record)
    pruned["radiant"] = [hero for hero in (record.get("radiant") or []) if hero in direct_radiant]
    pruned["dire"] = [hero for hero in (record.get("dire") or []) if hero in direct_dire]
    return pruned


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


def pre10_skirmish_burst_metrics(ctx, cluster):
    damage_signals = []
    for signal in cluster["signals"]:
        if signal.get("kind") != "damage":
            continue
        attacker = signal.get("attacker")
        target = signal.get("target")
        if not attacker or not target:
            continue
        attacker_team = ctx.unit_team(attacker)
        target_team = ctx.unit_team(target)
        if attacker_team not in {2, 3} or target_team not in {2, 3}:
            continue
        attacker_dist = distance_game(signal.get("attacker_pos"), signal.get("target_pos"))
        if attacker_dist is not None and attacker_dist > PARAMS["remote_contribution_distance_game"]:
            continue
        damage_signals.append({
            "time": signal["time"],
            "value": signal["value"],
            "target_hero": ctx.unit_hero(target),
        })

    if not damage_signals:
        return {"max_total_damage": 0, "max_single_hero_damage": 0}

    window_seconds = PARAMS["skirmish_pre10_burst_window_seconds"]
    best_total = 0
    best_single = 0
    for anchor in sorted({item["time"] for item in damage_signals}):
        window_end = anchor + window_seconds
        total = 0
        by_hero = defaultdict(float)
        for item in damage_signals:
            if anchor <= item["time"] < window_end:
                total += item["value"]
                by_hero[item["target_hero"]] += item["value"]
        best_total = max(best_total, total)
        best_single = max(best_single, max(by_hero.values(), default=0))
    return {
        "max_total_damage": round(best_total),
        "max_single_hero_damage": round(best_single),
    }


def is_valid_seed(ctx, cluster, stats, label=None):
    total_damage = stats["damage_radiant"] + stats["damage_dire"]
    if cluster["start"] < 600 and has_any_label(label or "", {"小规模冲突"}):
        if stats["deaths"]:
            return True
        duration = cluster["end"] - cluster["start"]
        max_single_hero_damage = max(stats.get("damage_taken_by_hero", {}).values(), default=0)
        if duration <= PARAMS["skirmish_pre10_short_duration_seconds"]:
            return (
                max_single_hero_damage > PARAMS["skirmish_pre10_single_hero_damage_threshold"]
                or total_damage > PARAMS["skirmish_pre10_total_damage_threshold"]
            )
        burst = pre10_skirmish_burst_metrics(ctx, cluster)
        return (
            burst["max_total_damage"] >= PARAMS["skirmish_pre10_burst_total_damage_threshold"]
            or burst["max_single_hero_damage"] >= PARAMS["skirmish_pre10_burst_single_hero_damage_threshold"]
        )
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


def record_stats_for_classification(record):
    return {
        "radiant": record.get("radiant") or [],
        "dire": record.get("dire") or [],
        "direct_radiant": record.get("direct_radiant") or [],
        "direct_dire": record.get("direct_dire") or [],
        "damage_radiant": record.get("damage_radiant", 0),
        "damage_dire": record.get("damage_dire", 0),
        "damage_taken_radiant": record.get("damage_taken_radiant", 0),
        "damage_taken_dire": record.get("damage_taken_dire", 0),
        "deaths": record.get("deaths") or [],
    }


def reclassify_merged_record(record):
    duration = record["end"] - record["start"]
    primary = classify_fight(record_stats_for_classification(record), duration)
    if not primary:
        return record

    old_label = record.get("label", "")
    old_labels = label_set(old_label)
    context_labels = [
        label.strip()
        for label in str(old_label).split("/")
        if label.strip() and label.strip() not in COMBAT_LABELS
    ]
    labels = [primary] + [label for label in context_labels if label != primary]
    new_label = " / ".join(labels)
    if new_label != old_label:
        record["evidence"] = (
            f"{record.get('evidence', '')}; reclassified_after_merge "
            f"from={sorted(old_labels)} to={new_label}"
        )
        record["label"] = new_label
    return record


def record_heroes(record):
    return set(record.get("radiant", [])) | set(record.get("dire", []))


def record_center(record):
    center = record.get("center_raw")
    if not center:
        return None
    return tuple(center)


def hero_unit_by_name(ctx):
    return {
        hero: unit
        for unit, hero in ctx.unit_to_cn.items()
        if str(unit).startswith("npc_dota_hero_")
    }


def record_hero_second_positions(ctx, pos_index, start, end, hero_names):
    hero_to_unit = hero_unit_by_name(ctx)
    positions = {}
    for t in range(max(0, start), end + 1):
        second_positions = {}
        for hero in hero_names:
            unit = hero_to_unit.get(hero)
            if not unit:
                continue
            pos = pos_index.nearest(unit, t, max_gap=2)
            if pos is not None:
                second_positions[hero] = [round(pos[0], 3), round(pos[1], 3)]
        if second_positions:
            positions[str(t)] = second_positions
    return positions


def record_hero_second_centers(ctx, pos_index, start, end, hero_names):
    hero_to_unit = hero_unit_by_name(ctx)
    centers = {}
    for t in range(max(0, start), end + 1):
        points = []
        for hero in hero_names:
            unit = hero_to_unit.get(hero)
            if not unit:
                continue
            pos = pos_index.nearest(unit, t, max_gap=2)
            if pos is not None:
                points.append(pos)
        center = average_position(points)
        if center is not None:
            centers[str(t)] = [round(center[0], 3), round(center[1], 3)]
    return centers


def cluster_interaction_positions(ctx, cluster):
    positions = []
    for signal in cluster["signals"]:
        attacker_team = ctx.unit_team(signal.get("attacker"))
        target_team = ctx.unit_team(signal.get("target"))
        if attacker_team not in {2, 3} or target_team not in {2, 3} or attacker_team == target_team:
            continue
        if signal.get("kind") not in {"damage", "control", "death"}:
            continue
        pos = signal.get("pos")
        if pos is None:
            continue
        interaction_distance = None
        if signal.get("attacker_pos") and signal.get("target_pos"):
            interaction_distance = distance_game(signal.get("attacker_pos"), signal.get("target_pos"))
        is_remote = (
            interaction_distance is not None
            and interaction_distance > PARAMS["remote_contribution_distance_game"]
        )
        positions.append({
            "time": signal["time"],
            "pos": [round(pos[0], 3), round(pos[1], 3)],
            "kind": signal.get("kind"),
            "attacker": ctx.unit_hero(signal.get("attacker")),
            "target": ctx.unit_hero(signal.get("target")),
            "distance_game": None if interaction_distance is None else round(interaction_distance),
            "is_remote": is_remote,
        })
    return positions


def record_hero_second_center(record, second):
    center = (record.get("hero_second_centers") or {}).get(str(second))
    if not center:
        return None
    return tuple(center)


def record_hero_positions_at(record, second, heroes=None):
    positions_by_second = record.get("hero_second_positions") or {}
    positions = positions_by_second.get(str(second)) or {}
    allowed = set(heroes) if heroes else None
    points = []
    for hero, pos in positions.items():
        if allowed is not None and hero not in allowed:
            continue
        if pos:
            points.append(tuple(pos))
    return points


def continuation_second_center(left, right, second, heroes):
    points = []
    points.extend(record_hero_positions_at(left, second, heroes))
    points.extend(record_hero_positions_at(right, second, heroes))
    return average_position(points)


def continuation_position_path(left, right, heroes):
    gap = right["start"] - left["end"]
    if gap <= 0:
        start = max(left["start"], right["start"])
        end = min(left["end"], right["end"])
    else:
        start = left["end"]
        end = right["start"]

    centers = []
    max_step = 0
    previous = None
    for second in range(start, end + 1):
        center = continuation_second_center(left, right, second, heroes)
        if center is None:
            continue
        if previous is not None:
            step = distance_game(previous, center)
            if step is not None:
                max_step = max(max_step, step)
                if step > PARAMS["merge_interaction_continuation_step_distance_game"]:
                    return None
        centers.append((second, center))
        previous = center

    if not centers:
        return None
    if gap > 0 and len(centers) < 2:
        return None
    return {
        "seconds": len(centers),
        "max_step_game": max_step,
        "start_second": centers[0][0],
        "end_second": centers[-1][0],
    }


def min_hero_second_center_distance(left, right):
    start = max(left["start"], right["start"])
    end = min(left["end"], right["end"])
    seconds = []
    if start <= end:
        seconds.extend(range(start, end + 1))
    else:
        seconds.extend([left["end"], right["start"]])

    distances = []
    for second in seconds:
        left_center = record_hero_second_center(left, second)
        right_center = record_hero_second_center(right, second)
        if left_center is None or right_center is None:
            continue
        distance = distance_game(left_center, right_center)
        if distance is not None:
            distances.append(distance)
    if not distances:
        return None
    return min(distances)


def should_merge_adjacent_records(left, right):
    gap = right["start"] - left["end"]
    if gap > PARAMS["merge_adjacent_gap_seconds"]:
        return False

    left_heroes = record_heroes(left)
    right_heroes = record_heroes(right)
    shared = left_heroes & right_heroes
    union = left_heroes | right_heroes
    jaccard = len(shared) / len(union) if union else 0

    hero_center_distance = min_hero_second_center_distance(left, right)
    if (
        hero_center_distance is not None
        and hero_center_distance <= PARAMS["merge_hero_center_distance_game"]
        and (
            len(shared) >= PARAMS["merge_hero_center_min_shared_heroes"]
            or jaccard >= PARAMS["merge_hero_center_min_jaccard"]
        )
    ):
        return True

    if gap < 0:
        return False
    if not (has_any_label(left["label"], {"团战"}) or has_any_label(right["label"], {"团战"})):
        return False

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


def merge_hero_second_centers(left, right):
    merged = dict(left.get("hero_second_centers") or {})
    for second, center in (right.get("hero_second_centers") or {}).items():
        if second in merged:
            merged[second] = [
                round((merged[second][0] + center[0]) / 2, 3),
                round((merged[second][1] + center[1]) / 2, 3),
            ]
        else:
            merged[second] = center
    return merged


def merge_hero_second_positions(left, right):
    merged = {
        second: dict(positions)
        for second, positions in (left.get("hero_second_positions") or {}).items()
    }
    for second, positions in (right.get("hero_second_positions") or {}).items():
        if second not in merged:
            merged[second] = dict(positions)
            continue
        merged[second].update(positions)
    return merged


def center_from_hero_positions(record, start, end):
    points = []
    for second_text, positions in (record.get("hero_second_positions") or {}).items():
        try:
            second = int(second_text)
        except (TypeError, ValueError):
            continue
        if second < start or second > end:
            continue
        for pos in positions.values():
            if pos:
                points.append(tuple(pos))
    return average_position(points)


def merge_interaction_positions(left, right):
    return sorted(
        (left.get("interaction_positions") or []) + (right.get("interaction_positions") or []),
        key=lambda item: (item.get("time", 0), item.get("kind", "")),
    )


def record_interaction_heroes(record, direct_only=False):
    heroes = set()
    for item in record.get("interaction_positions") or []:
        if direct_only and item.get("is_remote"):
            target = item.get("target")
            if target:
                heroes.add(target)
            continue
        for key in ("attacker", "target"):
            hero = item.get(key)
            if hero:
                heroes.add(hero)
    return heroes


def should_merge_interaction_continuation(left, right):
    gap = right["start"] - left["end"]
    if gap > interaction_continuation_gap_seconds_for_time(right["start"]):
        return False
    shared = record_interaction_heroes(left, direct_only=True) & record_interaction_heroes(right, direct_only=True)
    if not shared:
        return False
    return continuation_position_path(left, right, shared) is not None


def gank_sides(record):
    if not has_any_label(record.get("label", ""), {"GANK"}):
        return None
    radiant = set(record.get("radiant") or [])
    dire = set(record.get("dire") or [])
    if len(radiant) >= PARAMS["gank_min_attacker_side_heroes"] and len(dire) <= PARAMS["gank_max_victim_side_heroes"]:
        return {"attackers": radiant, "victims": dire}
    if len(dire) >= PARAMS["gank_min_attacker_side_heroes"] and len(radiant) <= PARAMS["gank_max_victim_side_heroes"]:
        return {"attackers": dire, "victims": radiant}
    return None


def should_merge_chase_record(left, right):
    gap = right["start"] - left["end"]
    if gap < 0 or gap > PARAMS["merge_chase_gap_seconds"]:
        return False
    sides = gank_sides(right)
    if not sides:
        return False
    left_heroes = record_heroes(left)
    if not (sides["victims"] & left_heroes):
        return False
    if len(sides["attackers"] & left_heroes) < PARAMS["merge_chase_min_shared_attackers"]:
        return False

    left_center = record_center(left)
    right_center = record_center(right)
    if left_center is not None and right_center is not None:
        if distance_game(left_center, right_center) <= PARAMS["merge_chase_center_distance_game"]:
            return True

    hero_center_distance = min_hero_second_center_distance(left, right)
    return (
        hero_center_distance is not None
        and hero_center_distance <= PARAMS["merge_chase_center_distance_game"]
    )


def remove_label(label_text, label_to_remove):
    labels = [label.strip() for label in str(label_text).split("/") if label.strip()]
    labels = [label for label in labels if label != label_to_remove]
    return " / ".join(labels)


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
        "hero_second_centers": merge_hero_second_centers(left, right),
        "hero_second_positions": merge_hero_second_positions(left, right),
        "interaction_positions": merge_interaction_positions(left, right),
        "evidence": f"{left.get('evidence', '')}; merged_adjacent_fight gap={right['start'] - left['end']}s; next_evidence=({right.get('evidence', '')})",
    }
    return reclassify_merged_record(merged)


def merge_chase_record(left, right):
    merged = merge_adjacent_record(left, right)
    merged["label"] = remove_label(merged["label"], "GANK") or left["label"]
    merged["evidence"] = (
        f"{merged.get('evidence', '')}; merged_chase_gank gap={right['start'] - left['end']}s "
        f"absorbed_time_range={right.get('time_range')}"
    )
    return reclassify_merged_record(merged)


def merge_interaction_continuation_record(left, right):
    merged = merge_adjacent_record(left, right)
    shared = sorted(record_interaction_heroes(left, direct_only=True) & record_interaction_heroes(right, direct_only=True))
    path = continuation_position_path(left, right, set(shared)) or {}
    updated_center = center_from_hero_positions(merged, merged["start"], merged["end"])
    if updated_center is not None:
        merged["center_raw"] = [round(updated_center[0], 3), round(updated_center[1], 3)]
    if has_any_label(right.get("label", ""), {"GANK"}):
        merged["label"] = remove_label(merged["label"], "GANK") or left["label"]
    merged["evidence"] = (
        f"{merged.get('evidence', '')}; merged_interaction_continuation "
        f"gap={right['start'] - left['end']}s shared_interaction_heroes={shared} "
        f"path_seconds={path.get('seconds')} path_max_step_game={path.get('max_step_game', 0):.0f} "
        f"absorbed_time_range={right.get('time_range')}"
    )
    return reclassify_merged_record(merged)


def merge_adjacent_fight_records(records):
    merged = []
    for record in sorted(records, key=lambda item: (item["start"], item["end"])):
        if merged and should_merge_interaction_continuation(merged[-1], record):
            merged[-1] = merge_interaction_continuation_record(merged[-1], record)
        elif merged and should_merge_chase_record(merged[-1], record):
            merged[-1] = merge_chase_record(merged[-1], record)
        elif merged and should_merge_adjacent_records(merged[-1], record):
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
    if record["start"] < PARAMS["roshan_context_min_time"]:
        return None
    if not has_any_label(record["label"], {"团战", "小规模冲突"}):
        return None
    opening_points = []
    opening_seconds = []
    hero_names = record_heroes(record)
    start = record["start"]
    end = min(record["end"], start + PARAMS["roshan_pit_opening_seconds"] - 1)
    for second in range(start, end + 1):
        points = record_hero_positions_at(record, second, hero_names)
        if not points:
            continue
        opening_seconds.append(second)
        opening_points.extend(points)
    opening_center = average_position(opening_points)
    if opening_center is None:
        return None
    best = None
    radius = PARAMS["roshan_pit_context_radius_game"]
    for pit_name, pit in ROSHAN_PITS.items():
        dist = distance_game(opening_center, pit["raw"])
        if dist is None or dist > radius:
            continue
        candidate = {
            "pit_name": pit_name,
            "distance_game": dist,
            "server": pit["server"],
            "raw": pit["raw"],
            "opening_center": [round(opening_center[0], 3), round(opening_center[1], 3)],
            "opening_start": opening_seconds[0],
            "opening_end": opening_seconds[-1],
            "opening_position_count": len(opening_points),
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
                f"opening_seconds={seconds_to_time(context['opening_start'])}-{seconds_to_time(context['opening_end'])} "
                f"opening_center=({context['opening_center'][0]:.3f},{context['opening_center'][1]:.3f}) "
                f"opening_position_count={context['opening_position_count']} "
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


def tower_context_center_for_record(record):
    hero_names = (
        set(record.get("direct_radiant") or [])
        | set(record.get("direct_dire") or [])
    ) - (
        set(record.get("remote_radiant") or [])
        | set(record.get("remote_dire") or [])
        | set(record.get("remote_then_direct_radiant") or [])
        | set(record.get("remote_then_direct_dire") or [])
    )
    if not hero_names:
        hero_names = record_heroes(record)
    start = record["start"]
    end = min(record["end"], start + PARAMS["tower_context_opening_seconds"] - 1)
    opening_points = []
    opening_seconds = []
    for second in range(start, end + 1):
        points = record_hero_positions_at(record, second, hero_names)
        if not points:
            continue
        opening_seconds.append(second)
        opening_points.extend(points)
    opening_center = average_position(opening_points)
    if opening_center is not None:
        return opening_center, opening_seconds, len(opening_points), "opening"
    center = record.get("center_raw")
    if center:
        return tuple(center), [], 0, "full"
    return None, [], 0, "none"


def tower_context_for_record(record, tower_context):
    center, opening_seconds, position_count, center_method = tower_context_center_for_record(record)
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
            "center_method": center_method,
            "opening_start": opening_seconds[0] if opening_seconds else None,
            "opening_end": opening_seconds[-1] if opening_seconds else None,
            "opening_position_count": position_count,
            "context_center": [round(center[0], 3), round(center[1], 3)],
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
                f"status_time={seconds_to_time(context['status_time'])} "
                f"center_method={context.get('center_method')} "
                f"context_center=({context['context_center'][0]:.3f},{context['context_center'][1]:.3f})"
                + (
                    f" opening_seconds={seconds_to_time(context['opening_start'])}-{seconds_to_time(context['opening_end'])} "
                    f"opening_position_count={context['opening_position_count']}"
                    if context.get("opening_start") is not None and context.get("opening_end") is not None
                    else ""
                )
            )
        annotated.append(record)
    return annotated


def build_fight_records(ctx):
    signals = build_signals(ctx)
    raw_clusters = cluster_signals(signals)
    pos_index = HeroPositionIndex(ctx)
    records = []
    for cluster in raw_clusters:
        stats = cluster_stats(ctx, cluster)
        stats_for_record = death_window_core_stats(ctx, cluster, stats)
        duration = cluster["end"] - cluster["start"]
        label = classify_fight(stats_for_record, duration)
        if not label or not is_valid_seed(ctx, cluster, stats, label):
            continue
        record_radiant = stats_for_record["radiant"]
        record_dire = stats_for_record["dire"]
        if label in {"单杀", "鍗曟潃"} and stats["deaths"]:
            record_radiant = stats_for_record["direct_radiant"] or stats_for_record["radiant"]
            record_dire = stats_for_record["direct_dire"] or stats_for_record["dire"]
        hero_names = record_radiant + record_dire
        position_pad = PARAMS["merge_interaction_continuation_position_pad_seconds"]
        record = {
            "match_id": ctx.match_id,
            "start": cluster["start"],
            "end": cluster["end"],
            "time_range": time_range(cluster["start"], cluster["end"]),
            "label": label,
            "center_raw": None if cluster["center"] is None else [round(cluster["center"][0], 3), round(cluster["center"][1], 3)],
            "radiant": record_radiant,
            "dire": record_dire,
            "remote_radiant": stats["remote_radiant"],
            "remote_dire": stats["remote_dire"],
            "remote_then_direct_radiant": stats["remote_then_direct_radiant"],
            "remote_then_direct_dire": stats["remote_then_direct_dire"],
            "direct_radiant": stats_for_record["direct_radiant"],
            "direct_dire": stats_for_record["direct_dire"],
            "damage_radiant": stats["damage_radiant"],
            "damage_dire": stats["damage_dire"],
            "damage_taken_radiant": stats["damage_taken_radiant"],
            "damage_taken_dire": stats["damage_taken_dire"],
            "damage_taken_by_hero": stats["damage_taken_by_hero"],
            "remote_damage_radiant": stats["remote_damage_radiant"],
            "remote_damage_dire": stats["remote_damage_dire"],
            "deaths": stats["deaths"],
            "signal_count": stats["signal_count"],
            "hero_second_centers": record_hero_second_centers(
                ctx,
                pos_index,
                cluster["start"] - position_pad,
                cluster["end"] + position_pad,
                hero_names,
            ),
            "hero_second_positions": record_hero_second_positions(
                ctx,
                pos_index,
                cluster["start"] - position_pad,
                cluster["end"] + position_pad,
                hero_names,
            ),
            "interaction_positions": cluster_interaction_positions(ctx, cluster),
            "evidence": evidence_text(cluster, stats),
        }
        records.append(prune_death_record_participants(record))
    records = merge_adjacent_fight_records(records)
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
