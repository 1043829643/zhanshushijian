import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDNAMES = ["id", "match_id", "labels", "confidence", "time_range", "heroes", "结果", "evidence", "批注"]

RUNE_NAMES = {
    0: "双倍神符",
    1: "急速神符",
    2: "幻象神符",
    3: "隐身神符",
    4: "恢复神符",
    5: "赏金神符",
    6: "奥术神符",
    7: "水符",
    8: "智慧神符",
    9: "护盾神符",
}

RESOURCE_TYPES = {
    "CHAT_MESSAGE_AEGIS": "肉山盾",
    "CHAT_MESSAGE_CHEESE": "奶酪",
    "CHAT_MESSAGE_REFRESHER_SHARD": "刷新碎片",
    "CHAT_MESSAGE_BANNER_PLANTED": "战旗",
    "CHAT_MESSAGE_MINIBOSS_KILL": "魔晶/折磨者",
}

LANE_ROLE = {
    2: {"bot": "优势路", "top": "劣势路", "mid": "中路"},
    3: {"top": "优势路", "bot": "劣势路", "mid": "中路"},
}

LANE_ZONE_SAMPLE_WINDOW = 600
LANE_ZONE_NEAR_ENEMY_CREEP_RAW = 8.0
LANE_ZONE_NEAR_ENEMY_DEATH_SECONDS = 3
LANE_ZONE_MARGIN_RAW = 10.0
LANE_ZONE_MIN_SAMPLES = 12
CUT_LANE_GROUP_GAP = 10
CUT_LANE_MIN_CREEP_DEATHS = 2
AGGRO_LANE_GROUP_GAP = 10


def read_jsonl(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path, rows):
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def to_int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def seconds_to_time(value):
    value = int(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 60:02d}:{value % 60:02d}"


def time_range(start, end=None):
    end = start if end is None else end
    return f"{seconds_to_time(start)}-{seconds_to_time(end)}"


def parse_time_text(value):
    match = re.match(r"(-?)(\d+):(\d+)$", str(value))
    if not match:
        return 0
    seconds = int(match.group(2)) * 60 + int(match.group(3))
    return -seconds if match.group(1) else seconds


def row_start_seconds(row):
    match = re.match(r"^(-?\d+:\d+)-", str(row.get("time_range", "00:00-00:00")))
    start = match.group(1) if match else "00:00"
    return parse_time_text(start)


def side_name(team):
    return "天辉" if int(team) == 2 else "夜魇" if int(team) == 3 else "中立"


def lane_from_xy(x, y):
    if y - x > 35:
        return "top"
    if x - y > 35:
        return "bot"
    return "mid"


def enemy_team(team):
    return 3 if team == 2 else 2 if team == 3 else None


def is_lane_creep_name(name):
    if not name:
        return False
    if name in ("npc_dota_creep_goodguys_melee", "npc_dota_creep_goodguys_ranged", "npc_dota_creep_goodguys_flagbearer"):
        return True
    if name in ("npc_dota_creep_badguys_melee", "npc_dota_creep_badguys_ranged", "npc_dota_creep_badguys_flagbearer"):
        return True
    return name in ("npc_dota_goodguys_siege", "npc_dota_badguys_siege")


def creep_team_from_name(name):
    if "goodguys" in str(name):
        return 2
    if "badguys" in str(name):
        return 3
    return None


def distance_sq(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(values) - 1)
    weight = pos - low
    return values[low] * (1 - weight) + values[high] * weight


def hero_unit_from_row(row, key):
    value = row.get(key)
    if value and value != "dota_unknown":
        return value
    source_key = "sourcename" if key == "attackername" else "targetsourcename"
    value = row.get(source_key)
    return value if value and value != "dota_unknown" else None


class MatchContext:
    def __init__(self, match_dir):
        self.match_dir = Path(match_dir)
        self.manifest = json.loads((self.match_dir / "manifest.json").read_text(encoding="utf-8"))
        self.match_id = str(self.manifest["match_id"])
        self.tables = {name: read_jsonl(self.match_dir / f"{name}.jsonl") for name in self.manifest["tables"]}
        self.players = self.tables.get("players", [])
        self.picks = self.tables.get("match_picks_bans", [])
        self.chat = self.tables.get("match_chat_events", [])
        self.combat = self.tables.get("combat_logs", [])
        self.intervals = self.tables.get("player_intervals2", [])

        self.slot_to_player = {to_int(row.get("slot")): row for row in self.players}
        self.hero_id_to_cn = {
            str(row.get("hero_id")): row.get("hero_name_cn")
            for row in self.picks
            if str(row.get("is_pick")).lower() == "true" and row.get("hero_id") and row.get("hero_name_cn")
        }
        self.unit_to_cn = {}
        self.unit_to_team = {}
        for row in self.players:
            name = self.hero_id_to_cn.get(str(row.get("hero_id"))) or row.get("hero_name") or f"slot{row.get('slot')}"
            unit = row.get("hero_name")
            if unit:
                self.unit_to_cn[unit] = name
                self.unit_to_team[unit] = to_int(row.get("team"))

    def slot_hero(self, slot):
        player = self.slot_to_player.get(to_int(slot))
        if not player:
            return f"slot{slot}"
        return self.hero_id_to_cn.get(str(player.get("hero_id"))) or player.get("hero_name") or f"slot{slot}"

    def slot_team(self, slot):
        player = self.slot_to_player.get(to_int(slot))
        return to_int(player.get("team")) if player else None

    def unit_hero(self, unit):
        if not unit:
            return ""
        return self.unit_to_cn.get(unit, unit.replace("npc_dota_hero_", ""))

    def unit_team(self, unit):
        return self.unit_to_team.get(unit)

    def heroes_text(self, radiant=None, dire=None):
        radiant = sorted(set(radiant or []))
        dire = sorted(set(dire or []))
        return f"天辉：{'、'.join(radiant) if radiant else '无'}；夜魇：{'、'.join(dire) if dire else '无'}"

    def hero_side_text(self, hero, team):
        return self.heroes_text([hero] if team == 2 else [], [hero] if team == 3 else [])


def event(match_id, label, confidence, start, heroes, result="", evidence="", end=None):
    confidence_value = round(confidence, 2) if isinstance(confidence, (int, float)) else confidence
    return {
        "id": 0,
        "match_id": match_id,
        "labels": label,
        "confidence": confidence_value,
        "time_range": time_range(start, end),
        "heroes": heroes,
        "结果": result,
        "evidence": evidence,
        "批注": "",
    }


def compute_laning_events(ctx):
    by_slot = defaultdict(list)
    for row in ctx.intervals:
        t = to_int(row.get("time"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        slot = to_int(row.get("slot"))
        if t is None or slot is None or x is None or y is None:
            continue
        if 0 <= t <= 300:
            by_slot[slot].append(lane_from_xy(x, y))

    events = []
    for slot in sorted(ctx.slot_to_player):
        samples = by_slot.get(slot, [])
        if not samples:
            continue
        lane_counts = {lane: samples.count(lane) for lane in ("top", "mid", "bot")}
        primary_lane = max(("top", "mid", "bot"), key=lambda lane: (lane_counts[lane], lane == "mid"))
        total = len(samples)
        lane_seconds = {lane: lane_counts[lane] * (300 / total) for lane in ("top", "mid", "bot")}
        secondary = [
            lane for lane in ("top", "mid", "bot")
            if lane != primary_lane and lane_seconds[lane] >= 60
        ]
        hero = ctx.slot_hero(slot)
        team = ctx.slot_team(slot)
        role_map = LANE_ROLE.get(team, {})
        primary_role = role_map.get(primary_lane, primary_lane)
        parts = [
            f"{hero}主分路：{primary_role}({primary_lane})",
            "路线停留：" + "，".join(f"{lane} {lane_seconds[lane]:.0f}s" for lane in ("top", "mid", "bot")),
        ]
        if secondary:
            parts.append("游走/次要路：" + "、".join(f"{role_map.get(lane, lane)}({lane}) {lane_seconds[lane]:.0f}s" for lane in secondary))
        events.append(event(
            ctx.match_id,
            "对线分路",
            "未知",
            0,
            ctx.hero_side_text(hero, team),
            "；".join(parts),
            f"player_intervals2 0<=time<=300 slot={slot} samples={total} counts={lane_counts}",
            300,
        ))
    return events


def compute_stack_events(ctx):
    rows = sorted(ctx.intervals, key=lambda row: (to_int(row.get("slot"), -1), to_int(row.get("time"), -10**9), to_int(row.get("log_index"), 0)))
    previous = {}
    events = []
    for row in rows:
        slot = to_int(row.get("slot"))
        if slot is None:
            continue
        camps = to_int(row.get("camps_stacked"), 0) or 0
        creeps = to_int(row.get("creeps_stacked"), 0) or 0
        old = previous.get(slot)
        previous[slot] = (camps, creeps)
        if not old:
            continue
        camp_delta = camps - old[0]
        creep_delta = creeps - old[1]
        if camp_delta <= 0 and creep_delta <= 0:
            continue
        hero = ctx.slot_hero(slot)
        team = ctx.slot_team(slot)
        t = to_int(row.get("time"), 0)
        events.append(event(
            ctx.match_id,
            "囤野/堆野",
            0.86,
            t,
            ctx.hero_side_text(hero, team),
            f"{hero} 堆野计数增加：camps +{max(camp_delta, 0)}，creeps +{max(creep_delta, 0)}",
            f"player_intervals2 slot={slot} log_index={row.get('log_index')} camps_stacked={camps} creeps_stacked={creeps}",
        ))
    return events


def lane_creep_deaths(ctx, max_time=600):
    deaths = []
    for row in ctx.combat:
        if row.get("type") != "DOTA_COMBATLOG_DEATH":
            continue
        t = to_int(row.get("time"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        target = row.get("targetname") or row.get("targetsourcename")
        team = creep_team_from_name(target)
        if t is None or x is None or y is None or not is_lane_creep_name(target) or team not in (2, 3):
            continue
        if 0 <= t <= max_time:
            deaths.append({
                "time": t,
                "log_index": to_int(row.get("log_index"), 0),
                "row": row,
                "target": target,
                "team": team,
                "lane": lane_from_xy(x, y),
                "x": x,
                "y": y,
            })
    return sorted(deaths, key=lambda item: (item["time"], item["log_index"]))


def build_lane_creep_status_context(ctx, max_time=650):
    by_time = defaultdict(list)
    by_ehandle = defaultdict(list)
    for row in ctx.tables.get("dota_model_neutral_siege_creep", []):
        if row.get("type") != "lane_creep_status":
            continue
        t = to_int(row.get("time"))
        team = to_int(row.get("team"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        ehandle = to_int(row.get("ehandle"))
        unit = row.get("unit")
        if t is None or x is None or y is None or ehandle is None or team not in (2, 3):
            continue
        if not (-5 <= t <= max_time):
            continue
        if not is_lane_creep_name(unit):
            continue
        item = {
            "time": t,
            "log_index": to_int(row.get("log_index"), 0),
            "unit": unit,
            "team": team,
            "ehandle": ehandle,
            "x": x,
            "y": y,
            "hp": to_float(row.get("hp"), 0) or 0,
            "life_state": to_int(row.get("lifeState"), 0) or 0,
            "lane_at_pos": lane_from_xy(x, y),
        }
        by_time[t].append(item)
        by_ehandle[ehandle].append(item)

    lane_by_ehandle = {}
    for ehandle, rows in by_ehandle.items():
        counts = defaultdict(int)
        for row in rows:
            counts[row["lane_at_pos"]] += 1
        if counts:
            lane_by_ehandle[ehandle] = max(("top", "mid", "bot"), key=lambda lane: counts[lane])

    return {"by_time": by_time, "by_ehandle": by_ehandle, "lane_by_ehandle": lane_by_ehandle}


def match_death_to_creep_status(death, status_context):
    best = None
    best_score = None
    point = (death["x"], death["y"])
    for t in range(death["time"] - 2, death["time"] + 3):
        for status in status_context["by_time"].get(t, []):
            if status["team"] != death["team"] or status["unit"] != death["target"]:
                continue
            dist = distance_sq(point, (status["x"], status["y"]))
            if dist > 9:
                continue
            dead_bonus = 0 if status["life_state"] == 1 or status["hp"] <= 0 else 1
            score = (dead_bonus, dist, abs(status["time"] - death["time"]))
            if best is None or score < best_score:
                best = status
                best_score = score
    return best


def enrich_deaths_with_status(deaths, status_context):
    enriched = []
    for death in deaths:
        match = match_death_to_creep_status(death, status_context)
        if match:
            death = dict(death)
            death["ehandle"] = match["ehandle"]
            death["lane"] = status_context["lane_by_ehandle"].get(match["ehandle"], death["lane"])
            death["status_match_time"] = match["time"]
            death["status_match_log_index"] = match["log_index"]
        enriched.append(death)
    return enriched


def build_t1_alive_lookup(ctx):
    by_tower = defaultdict(list)
    for row in ctx.tables.get("tower_status_update", []):
        team = to_int(row.get("team_num"))
        hp = to_float(row.get("hp"))
        max_hp = to_float(row.get("max_hp"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        if team not in (2, 3) or hp is None or max_hp is None or x is None or y is None:
            continue
        if int(max_hp) != 1800:
            continue
        lane = lane_from_xy(x, y)
        by_tower[(team, lane)].append((to_int(row.get("time"), -10**9), hp))

    for key in by_tower:
        by_tower[key].sort()

    def is_alive(team, lane, t):
        rows = by_tower.get((team, lane), [])
        if not rows:
            return True
        hp = rows[0][1]
        for row_time, row_hp in rows:
            if row_time <= t:
                hp = row_hp
            else:
                break
        return hp > 0

    return is_alive


def nearby_enemy_creep_count(death, deaths, status_context, radius=LANE_ZONE_NEAR_ENEMY_CREEP_RAW):
    radius_sq = radius * radius
    point = (death["x"], death["y"])
    count = 0
    source_parts = []
    enemy = enemy_team(death["team"])

    seen_ehandles = set()
    for t in range(death["time"] - 1, death["time"] + 2):
        for pos in status_context["by_time"].get(t, []):
            if pos["team"] != enemy:
                continue
            if status_context["lane_by_ehandle"].get(pos["ehandle"], pos["lane_at_pos"]) != death["lane"]:
                continue
            if pos["life_state"] == 1 or pos["hp"] <= 0:
                continue
            if distance_sq(point, (pos["x"], pos["y"])) <= radius_sq:
                seen_ehandles.add(pos["ehandle"])
    if seen_ehandles:
        count += len(seen_ehandles)
        source_parts.append(f"lane_creep_status_alive={len(seen_ehandles)}")

    death_count = 0
    if not seen_ehandles:
        for other in deaths:
            if other is death:
                continue
            if abs(other["time"] - death["time"]) > LANE_ZONE_NEAR_ENEMY_DEATH_SECONDS:
                continue
            if other["team"] != enemy or other["lane"] != death["lane"]:
                continue
            if distance_sq(point, (other["x"], other["y"])) <= radius_sq:
                death_count += 1
        if death_count:
            source_parts.append(f"near_enemy_deaths={death_count}")
        count += death_count
    return count, ",".join(source_parts) if source_parts else "none"


def build_lane_zones(ctx, deaths, status_context, t1_alive):
    samples = defaultdict(list)
    for death in deaths:
        if not t1_alive(2, death["lane"], death["time"]) or not t1_alive(3, death["lane"], death["time"]):
            continue
        near_count, _ = nearby_enemy_creep_count(death, deaths, status_context)
        if near_count > 0:
            samples[death["lane"]].append((death["x"], death["y"]))

    zones = {}
    for lane in ("top", "mid", "bot"):
        points = samples.get(lane, [])
        if len(points) < LANE_ZONE_MIN_SAMPLES:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        zones[lane] = {
            "source": "match_adaptive",
            "sample_count": len(points),
            "x_min": quantile(xs, 0.10) - LANE_ZONE_MARGIN_RAW,
            "x_max": quantile(xs, 0.90) + LANE_ZONE_MARGIN_RAW,
            "y_min": quantile(ys, 0.10) - LANE_ZONE_MARGIN_RAW,
            "y_max": quantile(ys, 0.90) + LANE_ZONE_MARGIN_RAW,
        }
    return zones


def in_lane_zone(death, zones):
    zone = zones.get(death["lane"])
    if not zone:
        return None
    return zone["x_min"] <= death["x"] <= zone["x_max"] and zone["y_min"] <= death["y"] <= zone["y_max"]


def build_xp_index(ctx, max_time=600):
    by_time = defaultdict(list)
    for row in ctx.combat:
        if row.get("type") != "DOTA_COMBATLOG_XP":
            continue
        t = to_int(row.get("time"))
        value = to_int(row.get("value"), 0)
        hero_unit = row.get("targetname")
        team = ctx.unit_team(hero_unit)
        if t is None or not (0 <= t <= max_time) or value <= 0 or team not in (2, 3):
            continue
        by_time[t].append({"unit": hero_unit, "hero": ctx.unit_hero(hero_unit), "team": team, "value": value, "log_index": to_int(row.get("log_index"), 0)})
    return by_time


def xp_heroes_for_death(death, xp_by_time, ctx):
    enemy = enemy_team(death["team"])
    heroes = {}
    for t in range(death["time"] - 1, death["time"] + 2):
        for xp in xp_by_time.get(t, []):
            if xp["team"] == enemy:
                heroes[xp["hero"]] = heroes.get(xp["hero"], 0) + xp["value"]
    return heroes


def group_lane_manipulation_candidates(candidates, gap, min_count=1):
    by_key = defaultdict(list)
    for cand in candidates:
        by_key[cand["key"]].append(cand)

    groups = []
    for key, items in by_key.items():
        items = sorted(items, key=lambda item: (item["time"], item["log_index"]))
        current = []
        for item in items:
            if current and item["time"] - current[-1]["time"] > gap:
                if len(current) >= min_count:
                    groups.append(current)
                current = []
            current.append(item)
        if len(current) >= min_count:
            groups.append(current)
    return groups


def compute_lane_manipulation_events(ctx):
    deaths = lane_creep_deaths(ctx)
    if not deaths:
        return []
    status_context = build_lane_creep_status_context(ctx)
    deaths = enrich_deaths_with_status(deaths, status_context)
    t1_alive = build_t1_alive_lookup(ctx)
    zones = build_lane_zones(ctx, deaths, status_context, t1_alive)
    xp_by_time = build_xp_index(ctx)

    cut_candidates = []
    aggro_candidates = []
    for death in deaths:
        if not t1_alive(death["team"], death["lane"], death["time"]):
            continue
        zone_state = in_lane_zone(death, zones)
        if zone_state is not False:
            continue
        near_count, near_source = nearby_enemy_creep_count(death, deaths, status_context)
        zone = zones.get(death["lane"])
        zone_text = (
            f"{zone['source']} samples={zone['sample_count']} bbox=({zone['x_min']:.1f},{zone['x_max']:.1f},{zone['y_min']:.1f},{zone['y_max']:.1f})"
            if zone else "missing"
        )
        base_evidence = (
            f"creep_death log_index={death['log_index']} target={death['target']} "
            f"ehandle={death.get('ehandle', 'unmatched')} "
            f"lane={death['lane']} xy=({death['x']:.1f},{death['y']:.1f}) "
            f"lane_zone={zone_text} near_enemy_creep_count={near_count} source={near_source}"
        )

        xp_heroes = xp_heroes_for_death(death, xp_by_time, ctx)
        if near_count > 0 and xp_heroes:
            hero_key = tuple(sorted(xp_heroes))
            aggro_candidates.append({
                **death,
                "key": (tuple(sorted(xp_heroes)), death["team"], death["lane"]),
                "xp_heroes": xp_heroes,
                "evidence": base_evidence,
            })

        attacker = hero_unit_from_row(death["row"], "attackername")
        attacker_team = ctx.unit_team(attacker)
        if near_count == 0 and attacker_team == enemy_team(death["team"]):
            cut_candidates.append({
                **death,
                "key": (attacker, death["team"], death["lane"]),
                "attacker": attacker,
                "attacker_hero": ctx.unit_hero(attacker),
                "attacker_team": attacker_team,
                "evidence": base_evidence + f" attacker={attacker}",
            })

    events = []
    for group in group_lane_manipulation_candidates(cut_candidates, CUT_LANE_GROUP_GAP, CUT_LANE_MIN_CREEP_DEATHS):
        first = group[0]
        hero = first["attacker_hero"]
        attacker_team = first["attacker_team"]
        victim_side = side_name(first["team"])
        lane = first["lane"]
        events.append(event(
            ctx.match_id,
            "断线",
            "未知",
            group[0]["time"],
            ctx.hero_side_text(hero, attacker_team),
            f"{hero}在{victim_side}{lane}路一塔存活时断兵；{len(group)}个{victim_side}{lane}路小兵死于lane_zone外，附近无敌方线上小兵",
            "；".join(item["evidence"] for item in group[:6]),
            group[-1]["time"],
        ))

    for group in group_lane_manipulation_candidates(aggro_candidates, AGGRO_LANE_GROUP_GAP, 1):
        first = group[0]
        xp_totals = defaultdict(int)
        for item in group:
            for hero, value in item["xp_heroes"].items():
                xp_totals[hero] += value
        xp_team = enemy_team(first["team"])
        xp_hero_names = sorted(xp_totals)
        victim_side = side_name(first["team"])
        result_heroes = "、".join(f"{hero}({xp_totals[hero]}xp)" for hero in xp_hero_names)
        events.append(event(
            ctx.match_id,
            "勾兵",
            "未知",
            group[0]["time"],
            ctx.heroes_text(xp_hero_names if xp_team == 2 else [], xp_hero_names if xp_team == 3 else []),
            f"{side_name(xp_team)}英雄获得{victim_side}{first['lane']}路小兵死亡经验：{result_heroes}；{len(group)}个小兵死于lane_zone外且附近有敌方线上小兵",
            "；".join(item["evidence"] for item in group[:6]),
            group[-1]["time"],
        ))

    return events


def compute_rune_events(ctx):
    chat = sorted(ctx.chat, key=lambda row: (to_int(row.get("time"), 0), to_int(row.get("log_index"), 0)))
    bottle_events = []
    events = []
    for row in chat:
        msg_type = row.get("type")
        if msg_type not in {"CHAT_MESSAGE_RUNE_PICKUP", "CHAT_MESSAGE_RUNE_BOTTLE", "CHAT_MESSAGE_RUNE_DENY"}:
            continue
        t = to_int(row.get("time"), 0)
        slot = to_int(row.get("player1"))
        rune_value = to_int(row.get("value"))
        rune = RUNE_NAMES.get(rune_value, f"未知神符({rune_value})")
        hero = ctx.slot_hero(slot)
        team = ctx.slot_team(slot)

        if msg_type == "CHAT_MESSAGE_RUNE_PICKUP":
            duplicate = any(old_slot == slot and old_value == rune_value and 0 <= t - old_time <= 90 for old_time, old_slot, old_value in bottle_events)
            if duplicate:
                continue
            action = "拾取"
        elif msg_type == "CHAT_MESSAGE_RUNE_BOTTLE":
            bottle_events.append((t, slot, rune_value))
            action = "罐装"
        else:
            action = "反补"

        events.append(event(
            ctx.match_id,
            "控符",
            0.9,
            t,
            ctx.hero_side_text(hero, team),
            f"{hero}{action}{rune}",
            f"match_chat_events {msg_type} value={rune_value} player1={slot} log_index={row.get('log_index')}",
        ))
    return events


def compute_smoke_events(ctx):
    events = []
    for row in sorted(ctx.combat, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0))):
        if row.get("type") not in {"DOTA_COMBATLOG_ITEM", "DOTA_COMBATLOG_ABILITY"}:
            continue
        if row.get("inflictor") != "item_smoke_of_deceit":
            continue
        unit = hero_unit_from_row(row, "attackername")
        hero = ctx.unit_hero(unit)
        team = ctx.unit_team(unit)
        t = to_int(row.get("time"), 0)
        events.append(event(
            ctx.match_id,
            "开雾",
            0.78,
            t,
            ctx.hero_side_text(hero, team),
            f"{hero} 使用诡计之雾",
            f"combat_logs item_smoke_of_deceit attacker={unit} log_index={row.get('log_index')}",
        ))
    return events


def compute_roshan_kill_events(ctx):
    death_rows = [
        row for row in ctx.combat
        if row.get("type") == "DOTA_COMBATLOG_DEATH" and row.get("targetname") == "npc_dota_roshan"
    ]
    chat_drops = [row for row in ctx.chat if row.get("type") in {"CHAT_MESSAGE_AEGIS", "CHAT_MESSAGE_CHEESE", "CHAT_MESSAGE_REFRESHER_SHARD", "CHAT_MESSAGE_BANNER_PLANTED"}]
    chat_kills = [row for row in ctx.chat if row.get("type") == "CHAT_MESSAGE_ROSHAN_KILL"]
    events = []

    for row in sorted(death_rows, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0))):
        t = to_int(row.get("time"), 0)
        attacker = hero_unit_from_row(row, "attackername")
        attacker_team = ctx.unit_team(attacker)
        side = side_name(attacker_team) if attacker_team in {2, 3} else "未知阵营"
        related = [drop for drop in chat_drops if 0 <= to_int(drop.get("time"), -999999) - t <= 20]
        heroes = []
        pieces = [f"{side}击杀肉山"]
        for drop in sorted(related, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0))):
            hero = ctx.slot_hero(drop.get("player1"))
            heroes.append(hero)
            pieces.append(f"{hero}获得{RESOURCE_TYPES.get(drop.get('type'), drop.get('type'))}")
        evidence_parts = [
            f"combat_logs roshan_death attacker={attacker} log_index={row.get('log_index')}",
        ]
        for chat in chat_kills:
            if abs(to_int(chat.get("time"), -999999) - t) <= 5:
                evidence_parts.append(f"match_chat_events CHAT_MESSAGE_ROSHAN_KILL log_index={chat.get('log_index')}")
        events.append(event(
            ctx.match_id,
            "肉山击杀",
            0.96,
            t,
            ctx.heroes_text(heroes if attacker_team == 2 else [], heroes if attacker_team == 3 else []),
            "；".join(pieces),
            "；".join(evidence_parts),
            max([t] + [to_int(drop.get("time"), t) for drop in related]),
        ))
    return events


def compute_resource_events(ctx):
    events = []
    for row in sorted(ctx.chat, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0))):
        msg_type = row.get("type")
        if msg_type not in RESOURCE_TYPES:
            continue
        # Roshan drops are merged into 肉山击杀 by compute_roshan_kill_events.
        if msg_type in {"CHAT_MESSAGE_AEGIS", "CHAT_MESSAGE_CHEESE", "CHAT_MESSAGE_REFRESHER_SHARD", "CHAT_MESSAGE_BANNER_PLANTED"}:
            continue
        t = to_int(row.get("time"), 0)
        slot = to_int(row.get("player1"))
        hero = ctx.slot_hero(slot)
        team = ctx.slot_team(slot)
        resource = RESOURCE_TYPES[msg_type]
        events.append(event(
            ctx.match_id,
            "资源获得",
            0.9,
            t,
            ctx.hero_side_text(hero, team),
            f"{hero}获得/击杀{resource}",
            f"match_chat_events {msg_type} player1={slot} value={row.get('value')} log_index={row.get('log_index')}",
        ))
    return events


def compute_aegis_death_events(ctx):
    aegis_by_slot = []
    for row in ctx.chat:
        if row.get("type") == "CHAT_MESSAGE_AEGIS":
            aegis_by_slot.append((to_int(row.get("time"), 0), to_int(row.get("player1")), row.get("log_index")))

    consumed = set()
    events = []
    deaths = [
        row for row in ctx.combat
        if row.get("type") == "DOTA_COMBATLOG_DEATH"
        and str(row.get("targethero")).lower() == "true"
    ]
    for row in sorted(deaths, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0))):
        t = to_int(row.get("time"), 0)
        target = hero_unit_from_row(row, "targetname")
        target_team = ctx.unit_team(target)
        target_hero = ctx.unit_hero(target)
        target_slot = None
        for slot, player in ctx.slot_to_player.items():
            if player.get("hero_name") == target:
                target_slot = slot
                break
        if target_slot is None:
            continue
        active = [
            item for item in aegis_by_slot
            if item[1] == target_slot and item not in consumed and 0 <= t - item[0] <= 300
        ]
        if not active:
            continue
        aegis = max(active, key=lambda item: item[0])
        consumed.add(aegis)
        events.append(event(
            ctx.match_id,
            "带盾阵亡",
            0.88,
            t,
            ctx.hero_side_text(target_hero, target_team),
            f"{target_hero}带盾阵亡；Aegis获取时间 {seconds_to_time(aegis[0])}",
            f"combat_logs death target={target} log_index={row.get('log_index')}；match_chat_events AEGIS log_index={aegis[2]}",
        ))
    return events


def assign_ids(rows):
    rows = sorted(rows, key=lambda row: (row_start_seconds(row), row["labels"], row["结果"]))
    for idx, row in enumerate(rows, start=1):
        row["id"] = idx
    return rows


def compute(match_dir, output_dir):
    ctx = MatchContext(match_dir)
    rows = []
    rows.extend(compute_laning_events(ctx))
    rows.extend(compute_stack_events(ctx))
    rows.extend(compute_lane_manipulation_events(ctx))
    rows.extend(compute_rune_events(ctx))
    rows.extend(compute_smoke_events(ctx))
    rows.extend(compute_roshan_kill_events(ctx))
    rows.extend(compute_resource_events(ctx))
    rows.extend(compute_aegis_death_events(ctx))
    rows = assign_ids(rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"fact_events_{ctx.match_id}.json"
    csv_path = output_dir / f"fact_events_{ctx.match_id}.csv"
    write_json(json_path, rows)
    write_csv(csv_path, rows)

    counts = defaultdict(int)
    for row in rows:
        counts[row["labels"]] += 1
    return {"match_id": ctx.match_id, "rows": len(rows), "counts": dict(sorted(counts.items())), "json": str(json_path), "csv": str(csv_path)}


def parse_args():
    parser = argparse.ArgumentParser(description="Compute first-pass fact tactical events from extracted StarRocks JSONL files.")
    parser.add_argument("match_dir", help="Directory produced by pipeline_extract_match.py, e.g. raw_db/match_8826099852")
    parser.add_argument("--out", default=str(ROOT / "computed_events"), help="Output directory.")
    return parser.parse_args()


def main():
    args = parse_args()
    summary = compute(Path(args.match_dir), Path(args.out))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
