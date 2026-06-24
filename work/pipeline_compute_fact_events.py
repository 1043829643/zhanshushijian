import argparse
import csv
import json
import math
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
LANE_CREEP_SOURCE_EARLY_SECONDS = 8
RUNE_NEAR_RADIUS_SERVER = 1400
SERVER_COORD_EXTENT = 16384
RUNE_SPOTS_SERVER = {
    "上符点": (-1640, 1110),
    "下符点": (1180, -1210),
}
BOUNTY_RUNE_SPOTS_SERVER = {
    "radiant_bounty": (590, -4637),
    "dire_bounty": (-1000, 4443),
}
WISDOM_RUNE_SPOTS_SERVER = {
    "radiant_wisdom": (-7468, 610),
    "dire_wisdom": (7537, -1285),
}
BOUNTY_RUNE_SPOTS_RAW = {
    "radiant_bounty": (133.6, 90.4),
    "dire_bounty": (120.0, 166.8),
}
WISDOM_RUNE_SPOTS_RAW = {
    "radiant_wisdom": (66.0, 134.0),
    "dire_wisdom": (191.0, 118.0),
}
RUNE_SPOT_DISPLAY = {
    "上符点": "上河道",
    "下符点": "下河道",
    "radiant_bounty": "天辉赏金符点",
    "dire_bounty": "夜魇赏金符点",
    "radiant_wisdom": "天辉智慧符点",
    "dire_wisdom": "夜魇智慧符点",
}
BOUNTY_RUNE_ALL_SPOTS_SERVER = {
    **RUNE_SPOTS_SERVER,
    **BOUNTY_RUNE_SPOTS_SERVER,
}
RAW_TO_SERVER_HOMOGRAPHY = (
    (0.0076578273910818, 0.0002417645481266, -0.4912248797627541),
    (-0.0000195675289368, 0.0078239058911833, -0.4804050188013505),
    (-0.0000504343416886, 0.0004340781970321, 1.0),
)
SMOKE_LINK_WINDOW = 120
LANE_ZONE_MARGIN_RAW = 10.0
LANE_ZONE_MIN_SAMPLES = 12
LANE_MANIP_TOWER_PROTECT_RAW = 12.0
CUT_LANE_GROUP_GAP = 10
CUT_LANE_MIN_CREEP_DEATHS = 2
AGGRO_LANE_GROUP_GAP = 10
PULL_LANE_MAX_TIME = 900
PULL_GROUP_GAP = 35
PULL_GROUP_DISTANCE_RAW = 28.0
PULL_STATUS_MATCH_WINDOW = 2
PULL_STATUS_POINT_DISTANCE_RAW = 20.0
PULL_STATUS_PAIR_DISTANCE_RAW = 18.0
PULL_STATUS_PROXIMITY_GAME = 550
PULL_STATUS_ANCHOR_PRE_WINDOW = 45
PULL_STATUS_ANCHOR_POST_WINDOW = 5
PULL_NEARBY_HERO_RADIUS_SERVER = 1500
PULL_NEARBY_HERO_TIME_PAD = 8
PULL_MIN_INTERACTIONS = 3
PULL_MIN_DEATHS = 2
PULL_STATUS_ONLY_MIN_SECONDS = 6
PULL_STATUS_ONLY_MIN_HP_DROP = 20
PULL_EXECUTOR_RADIUS_SERVER = 800
PULL_EXECUTOR_PRE_WINDOW = 30
PULL_EXECUTOR_POST_WINDOW = 10
ROSHAN_ATTEMPT_MIN_TIME = 600
ROSHAN_ATTEMPT_GROUP_GAP = 45
ROSHAN_ATTEMPT_DAMAGE_MIN = 1500
ROSHAN_ATTEMPT_KILL_GRACE = 8
ROSHAN_ATTEMPT_END_PAD = 10


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


def lane_display_name(lane):
    return {"top": "上路", "mid": "中路", "bot": "下路"}.get(lane, lane)


def hero_with_primary_lane(hero, hero_primary_lanes):
    lane_text = hero_primary_lanes.get(hero)
    return f"{hero}（{lane_text}）" if lane_text else hero


def format_xp_hero(hero, xp, hero_primary_lanes):
    lane_text = hero_primary_lanes.get(hero)
    if lane_text:
        return f"{hero}（{lane_text}，{xp}xp）"
    return f"{hero}({xp}xp)"


def raw_to_server_xy(x, y):
    h = RAW_TO_SERVER_HOMOGRAPHY
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    if not w:
        return None
    x_norm = (h[0][0] * x + h[0][1] * y + h[0][2]) / w
    y_norm = (h[1][0] * x + h[1][1] * y + h[1][2]) / w
    return (
        x_norm * SERVER_COORD_EXTENT - SERVER_COORD_EXTENT / 2,
        y_norm * SERVER_COORD_EXTENT - SERVER_COORD_EXTENT / 2,
    )


def server_distance(a, b):
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def raw_distance_to_game(distance_raw_value):
    if distance_raw_value is None:
        return None
    return distance_raw_value * 130


def is_lane_creep_name(name):
    if not name:
        return False
    if name in ("npc_dota_creep_goodguys_melee", "npc_dota_creep_goodguys_ranged", "npc_dota_creep_goodguys_flagbearer"):
        return True
    if name in ("npc_dota_creep_badguys_melee", "npc_dota_creep_badguys_ranged", "npc_dota_creep_badguys_flagbearer"):
        return True
    return name in ("npc_dota_goodguys_siege", "npc_dota_badguys_siege")


def is_neutral_creep_name(name):
    return bool(name) and str(name).startswith("npc_dota_neutral_")


def is_hero_unit_name(name):
    return bool(name) and str(name).startswith("npc_dota_hero_")


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


def is_roshan_name(name):
    return str(name) == "npc_dota_roshan"


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


def build_hero_primary_lanes(ctx):
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

    by_hero = {}
    for slot in sorted(ctx.slot_to_player):
        samples = by_slot.get(slot, [])
        if not samples:
            continue
        lane_counts = {lane: samples.count(lane) for lane in ("top", "mid", "bot")}
        primary_lane = max(("top", "mid", "bot"), key=lambda lane: (lane_counts[lane], lane == "mid"))
        by_hero[ctx.slot_hero(slot)] = lane_display_name(primary_lane)
    return by_hero


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
            xy_lane = lane_from_xy(x, y)
            deaths.append({
                "time": t,
                "log_index": to_int(row.get("log_index"), 0),
                "row": row,
                "target": target,
                "team": team,
                "lane": xy_lane,
                "death_xy_lane": xy_lane,
                "source_lane": xy_lane,
                "source_lane_method": "xy_fallback",
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
        raw_ehandle = to_int(row.get("ehandle"))
        log_index = to_int(row.get("log_index"), 0)
        ehandle = raw_ehandle if raw_ehandle is not None else -log_index
        unit = row.get("unit")
        if t is None or x is None or y is None or ehandle is None or team not in (2, 3):
            continue
        if not (-5 <= t <= max_time):
            continue
        if not is_lane_creep_name(unit):
            continue
        item = {
            "time": t,
            "log_index": log_index,
            "unit": unit,
            "team": team,
            "ehandle": ehandle,
            "has_ehandle": raw_ehandle is not None,
            "x": x,
            "y": y,
            "hp": to_float(row.get("hp"), 0) or 0,
            "life_state": to_int(row.get("lifeState"), 0) or 0,
            "lane_at_pos": lane_from_xy(x, y),
        }
        by_time[t].append(item)
        by_ehandle[ehandle].append(item)

    lane_by_ehandle = {}
    source_lane_by_ehandle = {}
    for ehandle, rows in by_ehandle.items():
        counts = defaultdict(int)
        for row in rows:
            counts[row["lane_at_pos"]] += 1
        if counts:
            lane_by_ehandle[ehandle] = max(("top", "mid", "bot"), key=lambda lane: counts[lane])
        sorted_rows = sorted(rows, key=lambda row: (row["time"], row["log_index"]))
        if sorted_rows and sorted_rows[0].get("has_ehandle"):
            first_time = sorted_rows[0]["time"]
            early_rows = [
                row for row in sorted_rows
                if row["time"] <= first_time + LANE_CREEP_SOURCE_EARLY_SECONDS
            ] or sorted_rows[:5]
            early_counts = defaultdict(int)
            for row in early_rows:
                early_counts[row["lane_at_pos"]] += 1
            if early_counts:
                source_lane_by_ehandle[ehandle] = max(("top", "mid", "bot"), key=lambda lane: early_counts[lane])

    return {
        "by_time": by_time,
        "by_ehandle": by_ehandle,
        "lane_by_ehandle": lane_by_ehandle,
        "source_lane_by_ehandle": source_lane_by_ehandle,
    }


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
            source_lane = (
                status_context["source_lane_by_ehandle"].get(match["ehandle"])
                if match.get("has_ehandle")
                else None
            )
            if source_lane:
                death["source_lane"] = source_lane
                death["source_lane_method"] = "ehandle_early_path"
                death["lane"] = source_lane
            else:
                death["source_lane"] = death.get("source_lane", death["lane"])
                death["source_lane_method"] = death.get("source_lane_method", "xy_fallback")
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


def build_t1_position_lookup(ctx):
    positions = {}
    for row in ctx.tables.get("tower_status_update", []):
        team = to_int(row.get("team_num"))
        max_hp = to_float(row.get("max_hp"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        if team not in (2, 3) or max_hp is None or x is None or y is None:
            continue
        if int(max_hp) != 1800:
            continue
        lane = lane_from_xy(x, y)
        key = (team, lane)
        row_time = to_int(row.get("time"), -10**9)
        if key not in positions or row_time < positions[key][0]:
            positions[key] = (row_time, x, y)
    return {key: (item[1], item[2]) for key, item in positions.items()}


def lane_t1s_alive(t1_alive, lane, t):
    return t1_alive(2, lane, t) and t1_alive(3, lane, t)


def near_alive_lane_t1(death, t1_positions, t1_alive, radius=LANE_MANIP_TOWER_PROTECT_RAW):
    point = (death["x"], death["y"])
    radius_sq = radius * radius
    best = None
    for team in (2, 3):
        pos = t1_positions.get((team, death["lane"]))
        if not pos:
            continue
        if not t1_alive(team, death["lane"], death["time"]):
            continue
        dist_sq = distance_sq(point, pos)
        if dist_sq > radius_sq:
            continue
        if best is None or dist_sq < best["distance_sq"]:
            best = {
                "team": team,
                "lane": death["lane"],
                "x": pos[0],
                "y": pos[1],
                "distance_sq": dist_sq,
            }
    return best


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
            pos_lane = status_context["source_lane_by_ehandle"].get(
                pos["ehandle"],
                status_context["lane_by_ehandle"].get(pos["ehandle"], pos["lane_at_pos"]),
            )
            if pos_lane != death["lane"]:
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


def reward_event(row, death_time, ctx):
    if row.get("type") != "DOTA_COMBATLOG_XP":
        return None
    t = to_int(row.get("time"))
    value = to_int(row.get("value"), 0)
    hero_unit = row.get("targetname")
    team = ctx.unit_team(hero_unit)
    if t is None or abs(t - death_time) > 1 or value <= 0 or team not in (2, 3):
        return None
    return {
        "unit": hero_unit,
        "hero": ctx.unit_hero(hero_unit),
        "team": team,
        "value": value,
        "log_index": to_int(row.get("log_index"), 0),
    }


def collect_reward_block(rows, death_index, direction, ctx):
    death_time = to_int(rows[death_index].get("time"), 0)
    rewards = []
    step = -1 if direction == "before" else 1
    idx = death_index + step
    while 0 <= idx < len(rows):
        row = rows[idx]
        row_type = row.get("type")
        if row_type == "DOTA_COMBATLOG_DEATH":
            break
        if row_type == "DOTA_COMBATLOG_GOLD" and abs((to_int(row.get("time"), death_time) or death_time) - death_time) <= 1:
            idx += step
            continue
        reward = reward_event(row, death_time, ctx)
        if reward:
            rewards.append(reward)
            idx += step
            continue
        break
    if direction == "before":
        rewards.reverse()
    return rewards


def build_death_xp_index(ctx, max_time=600, death_log_indexes=None):
    rows = sorted(ctx.combat, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0)))
    by_death_log_index = {}
    for index, row in enumerate(rows):
        if row.get("type") != "DOTA_COMBATLOG_DEATH":
            continue
        log_index = to_int(row.get("log_index"), 0)
        if death_log_indexes is not None and log_index not in death_log_indexes:
            continue
        t = to_int(row.get("time"))
        if t is None or not (0 <= t <= max_time):
            continue
        target = row.get("targetname")
        if not is_lane_creep_name(target):
            continue
        death_team = creep_team_from_name(target)
        enemy = enemy_team(death_team)
        rewards = collect_reward_block(rows, index, "before", ctx)
        if not rewards:
            rewards = collect_reward_block(rows, index, "after", ctx)
        heroes = {}
        log_indexes = []
        for reward in rewards:
            if reward["team"] != enemy:
                continue
            heroes[reward["hero"]] = heroes.get(reward["hero"], 0) + reward["value"]
            log_indexes.append(reward["log_index"])
        by_death_log_index[log_index] = {
            "heroes": heroes,
            "reward_log_indexes": log_indexes,
        }
    return by_death_log_index


def xp_heroes_for_death(death, death_xp_index):
    return death_xp_index.get(death["log_index"], {}).get("heroes", {})


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
    t1_positions = build_t1_position_lookup(ctx)
    hero_primary_lanes = build_hero_primary_lanes(ctx)
    eligible_deaths = [death for death in deaths if lane_t1s_alive(t1_alive, death["lane"], death["time"])]
    zones = build_lane_zones(ctx, eligible_deaths, status_context, t1_alive)
    death_xp_index = build_death_xp_index(
        ctx,
        death_log_indexes={death["log_index"] for death in eligible_deaths},
    )

    cut_candidates = []
    aggro_candidates = []
    for death in eligible_deaths:
        if near_alive_lane_t1(death, t1_positions, t1_alive):
            continue
        zone_state = in_lane_zone(death, zones)
        if zone_state is not False:
            continue
        near_count, near_source = nearby_enemy_creep_count(death, eligible_deaths, status_context)
        zone = zones.get(death["lane"])
        zone_text = (
            f"{zone['source']} samples={zone['sample_count']} bbox=({zone['x_min']:.1f},{zone['x_max']:.1f},{zone['y_min']:.1f},{zone['y_max']:.1f})"
            if zone else "missing"
        )
        base_evidence = (
            f"creep_death log_index={death['log_index']} target={death['target']} "
            f"ehandle={death.get('ehandle', 'unmatched')} "
            f"source_lane={death.get('source_lane', death['lane'])} "
            f"source_lane_method={death.get('source_lane_method', 'xy_fallback')} "
            f"death_xy_lane={death.get('death_xy_lane', death['lane'])} "
            f"lane={death['lane']} xy=({death['x']:.1f},{death['y']:.1f}) "
            f"both_t1_alive=true lane_zone={zone_text} near_enemy_creep_count={near_count} source={near_source}"
        )

        xp_heroes = xp_heroes_for_death(death, death_xp_index)
        if near_count > 0 and xp_heroes:
            reward_log_indexes = death_xp_index.get(death["log_index"], {}).get("reward_log_indexes", [])
            hero_key = tuple(sorted(xp_heroes))
            aggro_candidates.append({
                **death,
                "key": (tuple(sorted(xp_heroes)), death["team"], death["lane"]),
                "xp_heroes": xp_heroes,
                "evidence": base_evidence + f" reward_log_indexes={reward_log_indexes}",
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
        lane_text = lane_display_name(lane)
        hero_text = hero_with_primary_lane(hero, hero_primary_lanes)
        events.append(event(
            ctx.match_id,
            "断线",
            "未知",
            group[0]["time"],
            ctx.hero_side_text(hero, attacker_team),
            f"{hero_text}在{lane_text}断{victim_side}{lane_text}小兵；{len(group)}个{victim_side}{lane_text}小兵死于lane_zone外，附近无敌方线上小兵",
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
        lane_text = lane_display_name(first["lane"])
        result_heroes = "、".join(
            format_xp_hero(hero, xp_totals[hero], hero_primary_lanes)
            for hero in xp_hero_names
        )
        events.append(event(
            ctx.match_id,
            "勾兵",
            "未知",
            group[0]["time"],
            ctx.heroes_text(xp_hero_names if xp_team == 2 else [], xp_hero_names if xp_team == 3 else []),
            f"{side_name(xp_team)}英雄获得{victim_side}{lane_text}小兵死亡经验：{result_heroes}；{len(group)}个小兵死于lane_zone外且附近有敌方线上小兵",
            "；".join(item["evidence"] for item in group[:6]),
            group[-1]["time"],
        ))

    return events


def build_neutral_status_context(ctx, max_time=PULL_LANE_MAX_TIME + 5):
    by_time = defaultdict(list)
    for row in ctx.tables.get("dota_model_neutral_siege_creep", []):
        if row.get("type") != "neutral_status":
            continue
        t = to_int(row.get("time"))
        team = to_int(row.get("team"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        raw_ehandle = to_int(row.get("ehandle"))
        log_index = to_int(row.get("log_index"), 0)
        ehandle = raw_ehandle if raw_ehandle is not None else -log_index
        unit = row.get("unit")
        owner_class = row.get("ownerClass") or ""
        if t is None or x is None or y is None or ehandle is None:
            continue
        if not (-5 <= t <= max_time):
            continue
        if team != 4 or not is_neutral_creep_name(unit):
            continue
        if "Hero" in owner_class:
            continue
        by_time[t].append({
            "time": t,
            "log_index": log_index,
            "unit": unit,
            "team": team,
            "ehandle": ehandle,
            "has_ehandle": raw_ehandle is not None,
            "x": x,
            "y": y,
            "hp": to_float(row.get("hp"), 0) or 0,
            "life_state": to_int(row.get("lifeState"), 0) or 0,
            "owner_class": owner_class,
        })
    return {"by_time": by_time}


def statuses_around(by_time, time_value, unit=None, team=None, window=PULL_STATUS_MATCH_WINDOW):
    items = []
    for t in range(time_value - window, time_value + window + 1):
        for status in by_time.get(t, []):
            if unit is not None and status.get("unit") != unit:
                continue
            if team is not None and status.get("team") != team:
                continue
            items.append(status)
    return items


def best_status_near_point(statuses, point, max_distance):
    if point is None:
        return None
    best = None
    best_score = None
    max_distance_sq = max_distance * max_distance
    for status in statuses:
        dist = distance_sq(point, (status["x"], status["y"]))
        if dist > max_distance_sq:
            continue
        score = (dist, status["log_index"])
        if best is None or score < best_score:
            best = status
            best_score = score
    return best


def neutral_source_is_hero_controlled(ctx, row, neutral_side):
    unit_key = "attackername" if neutral_side == "attacker" else "targetname"
    source_key = "sourcename" if neutral_side == "attacker" else "targetsourcename"
    unit = row.get(unit_key)
    source = row.get(source_key)
    if not source or source == unit or source == "dota_unknown":
        return False
    return is_hero_unit_name(source) or ctx.unit_team(source) in (2, 3)


def pull_units_from_row(ctx, row):
    attacker = row.get("attackername")
    target = row.get("targetname")
    if is_lane_creep_name(attacker) and is_neutral_creep_name(target):
        neutral_side = "target"
        if neutral_source_is_hero_controlled(ctx, row, neutral_side):
            return None
        return {
            "lane_unit": attacker,
            "neutral_unit": target,
            "lane_side": "attacker",
            "neutral_side": neutral_side,
            "direction": "lane_to_neutral",
        }
    if is_neutral_creep_name(attacker) and is_lane_creep_name(target):
        neutral_side = "attacker"
        if neutral_source_is_hero_controlled(ctx, row, neutral_side):
            return None
        return {
            "lane_unit": target,
            "neutral_unit": attacker,
            "lane_side": "target",
            "neutral_side": neutral_side,
            "direction": "neutral_to_lane",
        }
    return None


def match_pull_statuses(lane_status_context, neutral_status_context, lane_unit, lane_team, neutral_unit, time_value, point):
    lane_statuses = statuses_around(
        lane_status_context["by_time"],
        time_value,
        unit=lane_unit,
        team=lane_team,
    )
    neutral_statuses = statuses_around(
        neutral_status_context["by_time"],
        time_value,
        unit=neutral_unit,
        team=4,
    )
    if not neutral_statuses:
        return None, None, point

    if point is not None:
        lane_status = best_status_near_point(lane_statuses, point, PULL_STATUS_POINT_DISTANCE_RAW)
        neutral_status = best_status_near_point(neutral_statuses, point, PULL_STATUS_POINT_DISTANCE_RAW)
        return lane_status, neutral_status, point

    best_pair = None
    best_score = None
    max_distance_sq = PULL_STATUS_PAIR_DISTANCE_RAW * PULL_STATUS_PAIR_DISTANCE_RAW
    for lane_status in lane_statuses:
        for neutral_status in neutral_statuses:
            dist = distance_sq((lane_status["x"], lane_status["y"]), (neutral_status["x"], neutral_status["y"]))
            if dist > max_distance_sq:
                continue
            score = (dist, abs(lane_status["time"] - time_value) + abs(neutral_status["time"] - time_value))
            if best_pair is None or score < best_score:
                best_pair = (lane_status, neutral_status)
                best_score = score
    if not best_pair:
        return None, None, None

    lane_status, neutral_status = best_pair
    inferred_point = (
        (lane_status["x"] + neutral_status["x"]) / 2,
        (lane_status["y"] + neutral_status["y"]) / 2,
    )
    return lane_status, neutral_status, inferred_point


def pull_interaction_candidates(ctx, lane_status_context, neutral_status_context):
    candidates = []
    for row in ctx.combat:
        row_type = row.get("type")
        if row_type not in ("DOTA_COMBATLOG_DAMAGE", "DOTA_COMBATLOG_DEATH"):
            continue
        t = to_int(row.get("time"))
        if t is None or not (0 <= t <= PULL_LANE_MAX_TIME):
            continue
        units = pull_units_from_row(ctx, row)
        if not units:
            continue
        lane_team = creep_team_from_name(units["lane_unit"])
        if lane_team not in (2, 3):
            continue

        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        point = (x, y) if x is not None and y is not None else None
        lane_status, neutral_status, point = match_pull_statuses(
            lane_status_context,
            neutral_status_context,
            units["lane_unit"],
            lane_team,
            units["neutral_unit"],
            t,
            point,
        )
        if neutral_status is None or point is None:
            continue

        if lane_status:
            lane = lane_status_context["lane_by_ehandle"].get(lane_status["ehandle"], lane_status["lane_at_pos"])
        else:
            lane = lane_from_xy(point[0], point[1])
        if lane == "mid":
            continue

        target = row.get("targetname")
        lane_death = row_type == "DOTA_COMBATLOG_DEATH" and target == units["lane_unit"]
        neutral_death = row_type == "DOTA_COMBATLOG_DEATH" and target == units["neutral_unit"]
        lane_status_text = lane_status["ehandle"] if lane_status else "unmatched"
        candidates.append({
            "time": t,
            "log_index": to_int(row.get("log_index"), 0),
            "row_type": row_type,
            "lane_team": lane_team,
            "lane": lane,
            "x": point[0],
            "y": point[1],
            "lane_unit": units["lane_unit"],
            "neutral_unit": units["neutral_unit"],
            "lane_ehandle": lane_status["ehandle"] if lane_status else None,
            "neutral_ehandle": neutral_status["ehandle"],
            "lane_hp": lane_status["hp"] if lane_status else None,
            "neutral_hp": neutral_status["hp"],
            "direction": units["direction"],
            "lane_death": lane_death,
            "neutral_death": neutral_death,
            "value": to_int(row.get("value"), 0) or 0,
            "evidence": (
                f"pull_interaction log_index={to_int(row.get('log_index'), 0)} type={row_type} "
                f"lane_unit={units['lane_unit']} neutral_unit={units['neutral_unit']} direction={units['direction']} "
                f"lane={lane} xy=({point[0]:.1f},{point[1]:.1f}) value={row.get('value')} "
                f"lane_status_ehandle={lane_status_text} neutral_status_ehandle={neutral_status['ehandle']}"
            ),
        })
    return sorted(candidates, key=lambda item: (item["time"], item["log_index"]))


def status_handle_text(status):
    if status is None:
        return "unmatched"
    prefix = "" if status.get("has_ehandle") else "synthetic:"
    return f"{prefix}{status.get('ehandle')}"


def pull_status_proximity_candidates(lane_status_context, neutral_status_context, anchors):
    best_by_bucket = {}
    max_distance_raw = PULL_STATUS_PROXIMITY_GAME / 130
    max_distance_sq = max_distance_raw * max_distance_raw

    def add_pair(t, lane_team, lane, lane_status, neutral_status, anchor=None):
        dist_sq = distance_sq(
            (lane_status["x"], lane_status["y"]),
            (neutral_status["x"], neutral_status["y"]),
        )
        if dist_sq > max_distance_sq:
            return

        center_x = (lane_status["x"] + neutral_status["x"]) / 2
        center_y = (lane_status["y"] + neutral_status["y"]) / 2
        bucket = (
            t,
            lane_team,
            lane,
            neutral_status["unit"],
            round(center_x / 5),
            round(center_y / 5),
        )
        score = (dist_sq, lane_status["log_index"], neutral_status["log_index"])
        previous = best_by_bucket.get(bucket)
        if previous and previous["score"] <= score:
            return

        dist = math.sqrt(dist_sq)
        dist_game = raw_distance_to_game(dist)
        anchor_text = (
            f"anchor_log_index={anchor['log_index']}"
            if anchor is not None
            else "anchor_log_index=status_scan"
        )
        best_by_bucket[bucket] = {
            "score": score,
            "item": {
                "time": t,
                "log_index": min(lane_status["log_index"], neutral_status["log_index"]),
                "row_type": "STATUS_PROXIMITY",
                "lane_team": lane_team,
                "lane": lane,
                "x": center_x,
                "y": center_y,
                "lane_unit": lane_status["unit"],
                "neutral_unit": neutral_status["unit"],
                "lane_ehandle": lane_status["ehandle"],
                "neutral_ehandle": neutral_status["ehandle"],
                "lane_hp": lane_status["hp"],
                "neutral_hp": neutral_status["hp"],
                "direction": "status_proximity",
                "lane_death": False,
                "neutral_death": False,
                "value": 0,
                "evidence": (
                    f"pull_status_proximity {anchor_text} time={t} "
                    f"lane_unit={lane_status['unit']} neutral_unit={neutral_status['unit']} "
                    f"lane={lane} xy=({center_x:.1f},{center_y:.1f}) "
                    f"distance_game={dist_game:.0f} radius_game={PULL_STATUS_PROXIMITY_GAME} "
                    f"lane_status_ehandle={status_handle_text(lane_status)} "
                    f"neutral_status_ehandle={status_handle_text(neutral_status)}"
                ),
            },
        }

    for anchor in anchors:
        if anchor.get("row_type") not in ("DOTA_COMBATLOG_DAMAGE", "DOTA_COMBATLOG_DEATH"):
            continue
        anchor_time = anchor["time"]
        lane_team = anchor["lane_team"]
        lane = anchor["lane"]
        neutral_unit = anchor["neutral_unit"]
        start = max(0, anchor_time - PULL_STATUS_ANCHOR_PRE_WINDOW)
        end = min(PULL_LANE_MAX_TIME, anchor_time + PULL_STATUS_ANCHOR_POST_WINDOW)
        for t in range(start, end + 1):
            lane_statuses = [
                status for status in lane_status_context["by_time"].get(t, [])
                if status["team"] == lane_team
            ]
            neutral_statuses = [
                status for status in neutral_status_context["by_time"].get(t, [])
                if status["unit"] == neutral_unit
            ]
            if not lane_statuses or not neutral_statuses:
                continue

            for lane_status in lane_statuses:
                status_lane = lane_status_context["lane_by_ehandle"].get(
                    lane_status["ehandle"],
                    lane_status_context["source_lane_by_ehandle"].get(
                        lane_status["ehandle"],
                        lane_status["lane_at_pos"],
                    ),
                )
                if status_lane != lane:
                    continue
                for neutral_status in neutral_statuses:
                    add_pair(t, lane_team, lane, lane_status, neutral_status, anchor=anchor)

    for t in sorted(lane_status_context["by_time"]):
        if not (0 <= t <= PULL_LANE_MAX_TIME):
            continue
        lane_statuses = lane_status_context["by_time"].get(t, [])
        neutral_statuses = neutral_status_context["by_time"].get(t, [])
        if not lane_statuses or not neutral_statuses:
            continue
        for lane_status in lane_statuses:
            lane_team = lane_status["team"]
            lane = lane_status_context["lane_by_ehandle"].get(
                lane_status["ehandle"],
                lane_status_context["source_lane_by_ehandle"].get(
                    lane_status["ehandle"],
                    lane_status["lane_at_pos"],
                ),
            )
            if lane_team not in (2, 3) or lane not in ("top", "bot"):
                continue
            for neutral_status in neutral_statuses:
                add_pair(t, lane_team, lane, lane_status, neutral_status)

    candidates = [entry["item"] for entry in best_by_bucket.values()]
    return sorted(candidates, key=lambda item: (item["time"], item["log_index"]))


def group_hp_drop(group, prefix):
    by_handle = defaultdict(list)
    for item in group:
        handle = item.get(f"{prefix}_ehandle")
        hp = item.get(f"{prefix}_hp")
        if handle is None or hp is None:
            continue
        by_handle[handle].append((item["time"], item["log_index"], hp))

    best_drop = 0
    for rows in by_handle.values():
        rows = sorted(rows)
        if not rows:
            continue
        max_seen = rows[0][2]
        for _, _, hp in rows[1:]:
            best_drop = max(best_drop, max_seen - hp)
            max_seen = max(max_seen, hp)
    return best_drop


def group_pull_candidates(candidates):
    by_key = defaultdict(list)
    for item in candidates:
        by_key[(item["lane_team"], item["lane"])].append(item)

    groups = []
    for items in by_key.values():
        current = []
        for item in sorted(items, key=lambda entry: (entry["time"], entry["log_index"])):
            if current:
                center_x = sum(entry["x"] for entry in current) / len(current)
                center_y = sum(entry["y"] for entry in current) / len(current)
                too_late = item["time"] - current[-1]["time"] > PULL_GROUP_GAP
                too_far = math.sqrt(distance_sq((center_x, center_y), (item["x"], item["y"]))) > PULL_GROUP_DISTANCE_RAW
                if too_late or too_far:
                    groups.append(current)
                    current = []
            current.append(item)
        if current:
            groups.append(current)
    return groups


def nearby_heroes_for_pull(ctx, center_raw, start, end):
    center_server = raw_to_server_xy(center_raw[0], center_raw[1])
    nearby = {2: set(), 3: set()}
    if center_server is None:
        return nearby
    start_window = start - PULL_NEARBY_HERO_TIME_PAD
    end_window = end + PULL_NEARBY_HERO_TIME_PAD
    for row in ctx.intervals:
        t = to_int(row.get("time"))
        if t is None or not (start_window <= t <= end_window):
            continue
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        slot = to_int(row.get("slot"))
        if x is None or y is None or slot is None:
            continue
        pos = raw_to_server_xy(x, y)
        dist = server_distance(center_server, pos)
        if dist is None or dist > PULL_NEARBY_HERO_RADIUS_SERVER:
            continue
        team = ctx.slot_team(slot)
        if team in nearby:
            nearby[team].add(ctx.slot_hero(slot))
    return nearby


def pull_executor_for_pull(ctx, center_raw, lane_team, start):
    center_server = raw_to_server_xy(center_raw[0], center_raw[1])
    if center_server is None:
        return None
    best = None
    start_window = start - PULL_EXECUTOR_PRE_WINDOW
    end_window = start + PULL_EXECUTOR_POST_WINDOW
    for row in ctx.intervals:
        t = to_int(row.get("time"))
        if t is None or not (start_window <= t <= end_window):
            continue
        slot = to_int(row.get("slot"))
        if slot is None or ctx.slot_team(slot) != lane_team:
            continue
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        if x is None or y is None:
            continue
        dist = server_distance(center_server, raw_to_server_xy(x, y))
        if dist is None or dist > PULL_EXECUTOR_RADIUS_SERVER:
            continue
        hero = ctx.slot_hero(slot)
        candidate = (dist, t, hero)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    dist, t, hero = best
    return {"hero": hero, "distance": dist, "time": t, "method": "position_nearest"}


def row_combat_owner(ctx, row, unit_key, source_key):
    unit = row.get(unit_key)
    source = row.get(source_key)
    for candidate in (source, unit):
        if not candidate:
            continue
        if is_hero_unit_name(candidate) or ctx.unit_team(candidate) in (2, 3):
            team = ctx.unit_team(candidate)
            if team in (2, 3):
                return {
                    "hero": ctx.unit_hero(candidate),
                    "team": team,
                    "unit": unit,
                    "source": source,
                }
    return None


def pull_executor_from_neutral_interaction(ctx, group, lane_team):
    start = group[0]["time"]
    end = min(group[-1]["time"], start + PULL_EXECUTOR_POST_WINDOW)
    start_window = start - PULL_EXECUTOR_PRE_WINDOW
    neutral_units = {item["neutral_unit"] for item in group if item.get("neutral_unit")}
    best = None
    for row in ctx.combat:
        t = to_int(row.get("time"))
        if t is None or not (start_window <= t <= end):
            continue
        attacker = row.get("attackername")
        target = row.get("targetname")
        attacker_is_pulled_neutral = attacker in neutral_units
        target_is_pulled_neutral = target in neutral_units
        if not attacker_is_pulled_neutral and not target_is_pulled_neutral:
            continue

        if target_is_pulled_neutral:
            owner = row_combat_owner(ctx, row, "attackername", "sourcename")
            direction = "hero_to_neutral"
        else:
            owner = row_combat_owner(ctx, row, "targetname", "targetsourcename")
            direction = "neutral_to_hero"
        if not owner or owner["team"] != lane_team:
            continue

        neutral_unit = target if target_is_pulled_neutral else attacker
        # Prefer interaction before pull start, then closest to start, then earlier log index.
        after_start_penalty = 1 if t > start else 0
        score = (after_start_penalty, abs(t - start), to_int(row.get("log_index"), 0))
        if best is None or score < best["score"]:
            best = {
                "score": score,
                "hero": owner["hero"],
                "time": t,
                "method": "neutral_interaction",
                "direction": direction,
                "neutral_unit": neutral_unit,
                "log_index": to_int(row.get("log_index"), 0),
                "row_type": row.get("type"),
            }
    if best is None:
        return None
    best.pop("score", None)
    return best


def compute_pull_events(ctx):
    lane_status_context = build_lane_creep_status_context(ctx, max_time=PULL_LANE_MAX_TIME + 5)
    neutral_status_context = build_neutral_status_context(ctx, max_time=PULL_LANE_MAX_TIME + 5)
    combat_candidates = pull_interaction_candidates(ctx, lane_status_context, neutral_status_context)
    candidates = sorted(
        combat_candidates
        + pull_status_proximity_candidates(lane_status_context, neutral_status_context, combat_candidates),
        key=lambda item: (item["time"], item["log_index"]),
    )
    events = []
    for group in group_pull_candidates(candidates):
        interaction_count = len(group)
        lane_deaths = sum(1 for item in group if item["lane_death"])
        neutral_deaths = sum(1 for item in group if item["neutral_death"])
        death_count = lane_deaths + neutral_deaths
        if interaction_count < PULL_MIN_INTERACTIONS and death_count < PULL_MIN_DEATHS:
            continue
        has_combat_anchor = any(
            item["row_type"] in ("DOTA_COMBATLOG_DAMAGE", "DOTA_COMBATLOG_DEATH")
            for item in group
        )
        if not has_combat_anchor:
            unique_seconds = len({item["time"] for item in group})
            lane_hp_drop = group_hp_drop(group, "lane")
            neutral_hp_drop = group_hp_drop(group, "neutral")
            if unique_seconds < PULL_STATUS_ONLY_MIN_SECONDS:
                continue
            if lane_hp_drop < PULL_STATUS_ONLY_MIN_HP_DROP or neutral_hp_drop < PULL_STATUS_ONLY_MIN_HP_DROP:
                continue

        first = group[0]
        lane = first["lane"]
        lane_team = first["lane_team"]
        center_raw = (
            sum(item["x"] for item in group) / len(group),
            sum(item["y"] for item in group) / len(group),
        )
        nearby = nearby_heroes_for_pull(ctx, center_raw, group[0]["time"], group[-1]["time"])
        nearby_radiant = sorted(nearby[2])
        nearby_dire = sorted(nearby[3])
        nearby_text = f"附近天辉英雄：{'、'.join(nearby_radiant) if nearby_radiant else '无'}；附近夜魇英雄：{'、'.join(nearby_dire) if nearby_dire else '无'}"
        executor = (
            pull_executor_from_neutral_interaction(ctx, group, lane_team)
            or pull_executor_for_pull(ctx, center_raw, lane_team, group[0]["time"])
        )
        if executor:
            heroes = ctx.hero_side_text(executor["hero"], lane_team)
            executor_evidence = (
                f"pull_executor hero={executor['hero']} method={executor['method']} "
                f"time={executor['time']} distance={executor.get('distance', '')} "
                f"direction={executor.get('direction', '')} neutral_unit={executor.get('neutral_unit', '')} "
                f"log_index={executor.get('log_index', '')}"
            )
        else:
            heroes = ctx.hero_side_text("未知", lane_team)
            executor_evidence = "pull_executor hero=unknown method=none"
        side = side_name(lane_team)
        lane_text = lane_display_name(lane)
        events.append(event(
            ctx.match_id,
            "拉野",
            "未知",
            group[0]["time"],
            heroes,
            (
                f"{side}{lane_text}小兵与中立野怪发生连续交战，判定为{side}{lane_text}拉野；"
                f"交互数 {interaction_count}，线兵死亡 {lane_deaths}，野怪死亡 {neutral_deaths}；{nearby_text}"
            ),
            executor_evidence + "；" + "；".join(item["evidence"] for item in group[:8]),
            group[-1]["time"],
        ))
    return events


def build_slot_position_index(ctx):
    by_slot = defaultdict(list)
    for row in ctx.intervals:
        slot = to_int(row.get("slot"))
        t = to_int(row.get("time"))
        x = to_float(row.get("x"))
        y = to_float(row.get("y"))
        if slot is None or t is None or x is None or y is None:
            continue
        server_pos = raw_to_server_xy(x, y)
        if server_pos is None:
            continue
        by_slot[slot].append({
            "time": t,
            "raw": (x, y),
            "server": server_pos,
        })
    for rows in by_slot.values():
        rows.sort(key=lambda item: item["time"])
    return by_slot


def nearest_slot_position(position_index, slot, time_value, max_gap=6):
    rows = position_index.get(slot) or []
    if not rows:
        return None
    best = min(rows, key=lambda item: abs(item["time"] - time_value))
    if abs(best["time"] - time_value) > max_gap:
        return None
    return best


def rune_spot_presence_text(ctx, position_index, time_value, spots=None):
    spots = spots or RUNE_SPOTS_SERVER
    spot_results = {}
    evidence_parts = []
    text_parts = []
    for spot_name, spot_pos in spots.items():
        by_team = {2: [], 3: []}
        distances = []
        for slot, player in ctx.slot_to_player.items():
            pos = nearest_slot_position(position_index, slot, time_value)
            if not pos:
                continue
            dist = server_distance(pos["server"], spot_pos)
            if dist is None or dist > RUNE_NEAR_RADIUS_SERVER:
                continue
            hero = ctx.slot_hero(slot)
            team = to_int(player.get("team"))
            if team in by_team:
                by_team[team].append(hero)
                distances.append(f"{hero}:{int(round(dist))}")
        for team in by_team:
            by_team[team].sort()
        spot_results[spot_name] = by_team
        evidence_parts.append(f"{spot_name} server={spot_pos} nearby_distances={distances}")
        display = RUNE_SPOT_DISPLAY.get(spot_name, spot_name)
        text_parts.append(
            f"{display}{RUNE_NEAR_RADIUS_SERVER}码：天辉"
            f"{'、'.join(by_team[2]) if by_team[2] else '无'}，夜魇"
            f"{'、'.join(by_team[3]) if by_team[3] else '无'}"
        )

    radiant = sorted(set(hero for by_team in spot_results.values() for hero in by_team[2]))
    dire = sorted(set(hero for by_team in spot_results.values() for hero in by_team[3]))
    text = "；".join(text_parts)
    return text, radiant, dire, "；".join(evidence_parts)


def rune_spot_for_slot(position_index, slot, time_value, spots=None):
    spots = spots or RUNE_SPOTS_SERVER
    pos = nearest_slot_position(position_index, slot, time_value)
    if not pos:
        return "", "actor_rune_spot=missing_position"

    best_name = None
    best_dist = None
    for spot_name, spot_pos in spots.items():
        dist = server_distance(pos["server"], spot_pos)
        if dist is None:
            continue
        if best_dist is None or dist < best_dist:
            best_name = spot_name
            best_dist = dist

    if best_name is None or best_dist > RUNE_NEAR_RADIUS_SERVER:
        distance_text = "unknown" if best_dist is None else int(round(best_dist))
        return "", f"actor_rune_spot=outside_configured_spots actor_nearest_distance={distance_text}"

    return (
        RUNE_SPOT_DISPLAY.get(best_name, best_name),
        f"actor_rune_spot={best_name} actor_rune_spot_display={RUNE_SPOT_DISPLAY.get(best_name, best_name)} "
        f"actor_distance={int(round(best_dist))}",
    )


def compute_rune_events(ctx):
    chat = sorted(ctx.chat, key=lambda row: (to_int(row.get("time"), 0), to_int(row.get("log_index"), 0)))
    position_index = build_slot_position_index(ctx)
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
        if rune_value == 5:
            spot_set = BOUNTY_RUNE_ALL_SPOTS_SERVER
        elif rune_value == 8:
            spot_set = WISDOM_RUNE_SPOTS_SERVER
        else:
            spot_set = RUNE_SPOTS_SERVER

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

        actor_spot, actor_spot_evidence = rune_spot_for_slot(position_index, slot, t, spot_set)
        presence_text, radiant_near, dire_near, presence_evidence = rune_spot_presence_text(ctx, position_index, t, spot_set)
        if team == 2:
            radiant_near = sorted(set(radiant_near + [hero]))
        elif team == 3:
            dire_near = sorted(set(dire_near + [hero]))
        events.append(event(
            ctx.match_id,
            "控符",
            0.9,
            t,
            ctx.heroes_text(radiant_near, dire_near),
            f"{hero}{action}{actor_spot}{rune}；符点附近：{presence_text}",
            (
                f"match_chat_events {msg_type} value={rune_value} player1={slot} log_index={row.get('log_index')}; "
                f"rune_spot_radius={RUNE_NEAR_RADIUS_SERVER}; transform=homography_to_server; "
                f"{actor_spot_evidence}; {presence_evidence}"
            ),
        ))
    return events


def compute_smoke_events(ctx):
    smoke_adds = []
    for row in sorted(ctx.combat, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0))):
        if row.get("type") != "DOTA_COMBATLOG_MODIFIER_ADD":
            continue
        if row.get("inflictor") != "modifier_smoke_of_deceit":
            continue
        if str(row.get("targethero")).lower() != "true" or str(row.get("targetillusion")).lower() == "true":
            continue
        target_unit = hero_unit_from_row(row, "targetname")
        if not target_unit:
            continue
        smoke_adds.append({
            "time": to_int(row.get("time"), 0),
            "log_index": to_int(row.get("log_index"), 0) or 0,
            "target_unit": target_unit,
            "target_team": ctx.unit_team(target_unit),
        })

    smoke_uses = []
    for row in sorted(ctx.combat, key=lambda item: (to_int(item.get("time"), 0), to_int(item.get("log_index"), 0))):
        if row.get("type") not in {"DOTA_COMBATLOG_ITEM", "DOTA_COMBATLOG_ABILITY"}:
            continue
        if row.get("inflictor") != "item_smoke_of_deceit":
            continue
        unit = hero_unit_from_row(row, "attackername")
        smoke_uses.append({
            "row": row,
            "time": to_int(row.get("time"), 0),
            "unit": unit,
            "team": ctx.unit_team(unit),
        })

    events = []
    for index, smoke_use in enumerate(smoke_uses):
        row = smoke_use["row"]
        unit = smoke_use["unit"]
        hero = ctx.unit_hero(unit)
        team = smoke_use["team"]
        t = smoke_use["time"]
        next_team_smoke_time = None
        for later in smoke_uses[index + 1:]:
            if later["team"] == team:
                next_team_smoke_time = later["time"]
                break
        window_end = t + SMOKE_LINK_WINDOW
        if next_team_smoke_time is not None:
            window_end = min(window_end, next_team_smoke_time - 1)

        smoked_units = {}
        add_log_indexes = []
        for add in smoke_adds:
            if add["time"] < t or add["time"] > window_end:
                continue
            if add["target_team"] != team:
                continue
            if add["target_unit"] in smoked_units:
                continue
            smoked_units[add["target_unit"]] = add["time"]
            add_log_indexes.append(add["log_index"])

        if unit and unit not in smoked_units:
            smoked_units[unit] = t
        smoked_heroes = sorted(ctx.unit_hero(target_unit) for target_unit in smoked_units if target_unit)
        events.append(event(
            ctx.match_id,
            "开雾",
            0.78,
            t,
            ctx.heroes_text(smoked_heroes if team == 2 else [], smoked_heroes if team == 3 else []),
            f"{hero} 使用诡计之雾；进入雾：{'、'.join(smoked_heroes) if smoked_heroes else '未识别'}",
            (
                f"combat_logs item_smoke_of_deceit attacker={unit} log_index={row.get('log_index')}; "
                f"modifier_smoke_of_deceit add_log_indexes={add_log_indexes}; link_window={t}-{window_end}"
            ),
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


def compute_roshan_attempt_events(ctx):
    damage_rows = []
    roshan_death_times = []
    chat_kill_times = []
    for row in ctx.combat:
        row_type = row.get("type")
        t = to_int(row.get("time"))
        if t is None:
            continue
        if row_type == "DOTA_COMBATLOG_DEATH" and (
            is_roshan_name(row.get("targetname")) or is_roshan_name(row.get("targetsourcename"))
        ):
            roshan_death_times.append(t)
            continue
        if row_type != "DOTA_COMBATLOG_DAMAGE":
            continue
        if not (is_roshan_name(row.get("targetname")) or is_roshan_name(row.get("targetsourcename"))):
            continue
        if t < ROSHAN_ATTEMPT_MIN_TIME:
            continue
        damage = to_float(row.get("value"), 0) or 0
        if damage <= 0:
            continue
        attacker = hero_unit_from_row(row, "attackername")
        if not attacker or ctx.unit_team(attacker) not in (2, 3):
            continue
        damage_rows.append({
            "time": t,
            "log_index": to_int(row.get("log_index"), 0),
            "attacker": attacker,
            "hero": ctx.unit_hero(attacker),
            "team": ctx.unit_team(attacker),
            "damage": damage,
        })

    for row in ctx.chat:
        if row.get("type") == "CHAT_MESSAGE_ROSHAN_KILL":
            t = to_int(row.get("time"))
            if t is not None:
                chat_kill_times.append(t)

    groups = []
    for item in sorted(damage_rows, key=lambda row: (row["time"], row["log_index"])):
        if not groups or item["time"] - groups[-1][-1]["time"] > ROSHAN_ATTEMPT_GROUP_GAP:
            groups.append([item])
        else:
            groups[-1].append(item)

    events = []
    for group in groups:
        start = group[0]["time"]
        last_damage_time = group[-1]["time"]
        if any(start <= death_time <= last_damage_time + ROSHAN_ATTEMPT_KILL_GRACE for death_time in roshan_death_times):
            continue
        if any(start <= kill_time <= last_damage_time + ROSHAN_ATTEMPT_KILL_GRACE for kill_time in chat_kill_times):
            continue

        total_damage = sum(item["damage"] for item in group)
        if total_damage < ROSHAN_ATTEMPT_DAMAGE_MIN:
            continue

        heroes_by_team = defaultdict(set)
        damage_by_team = defaultdict(float)
        for item in group:
            heroes_by_team[item["team"]].add(item["hero"])
            damage_by_team[item["team"]] += item["damage"]

        heroes = ctx.heroes_text(heroes_by_team.get(2), heroes_by_team.get(3))
        active_teams = sorted(team for team, damage in damage_by_team.items() if damage > 0)
        if len(active_teams) > 1:
            team_parts = []
            for team in active_teams:
                hero_list = "、".join(sorted(heroes_by_team.get(team, [])))
                team_parts.append(f"{side_name(team)}：{hero_list}，伤害{int(round(damage_by_team[team]))}")
            result = f"双方围绕肉山尝试/争夺，{'; '.join(team_parts)}；Roshan总伤害{int(round(total_damage))}，未完成击杀"
        else:
            main_team = active_teams[0]
            hero_list = "、".join(sorted(heroes_by_team.get(main_team, [])))
            result = (
                f"{side_name(main_team)}尝试击杀肉山，参与英雄：{hero_list}；"
                f"Roshan总伤害{int(round(total_damage))}，未完成击杀"
            )
        evidence = (
            f"combat_logs Roshan damage group start_log_index={group[0]['log_index']} "
            f"end_log_index={group[-1]['log_index']} hits={len(group)} "
            f"damage={int(round(total_damage))}; no roshan death within {ROSHAN_ATTEMPT_KILL_GRACE}s"
        )
        events.append(event(
            ctx.match_id,
            "肉山尝试",
            0.72,
            start,
            heroes,
            result,
            evidence,
            last_damage_time + ROSHAN_ATTEMPT_END_PAD,
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
    rows.extend(compute_pull_events(ctx))
    rows.extend(compute_lane_manipulation_events(ctx))
    rows.extend(compute_rune_events(ctx))
    rows.extend(compute_smoke_events(ctx))
    rows.extend(compute_roshan_kill_events(ctx))
    rows.extend(compute_roshan_attempt_events(ctx))
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
