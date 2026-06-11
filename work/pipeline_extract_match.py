import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path

import pymysql


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "raw_db"

CORE_TABLES = [
    "match_info",
    "players",
    "match_picks_bans",
    "player_intervals2",
    "combat_logs",
    "match_chat_events",
    "tower_status_update",
    "hero_status_update",
    "other_unit_sync",
    "dota_model_neutral_siege_creep",
    "hero_roshan_miniboss_vtord",
    "ward_placed_left_fact",
]

ACTION_TABLES = [
    "dota_player_orders",
    "dota_replay_actions",
    "player_actions",
]

TABLE_TIME_LIMITS = {
    "dota_model_neutral_siege_creep": (-5, 650),
}


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


def connect():
    return pymysql.connect(
        host=require_env("DB_HOST"),
        port=int(os.environ.get("DB_PORT", "9030")),
        user=require_env("DB_USER"),
        password=require_env("DB_PASS"),
        database=os.environ.get("DB_NAME", "dota2_analysis"),
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=300,
        cursorclass=pymysql.cursors.SSDictCursor,
    )


def table_has_column(conn, table_name, column_name):
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(*)
            from information_schema.columns
            where table_schema=database()
              and table_name=%s
              and column_name=%s
            """,
            (table_name, column_name),
        )
        row = cur.fetchone()
        return bool(row["count(*)"])


def detect_match_dts(conn, table_name, match_id):
    if not table_has_column(conn, table_name, "dt"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            f"select distinct dt from {table_name} where match_id=%s order by dt",
            (match_id,),
        )
        return [row["dt"].isoformat() if hasattr(row["dt"], "isoformat") else str(row["dt"]) for row in cur]


def write_query(conn, table_name, match_id, output_path, dt=None, limit=None):
    where = ["match_id=%s"]
    params = [match_id]
    if dt and table_has_column(conn, table_name, "dt"):
        where.append("dt=%s")
        params.append(dt)
    if table_name in TABLE_TIME_LIMITS and table_has_column(conn, table_name, "time"):
        start, end = TABLE_TIME_LIMITS[table_name]
        where.append("time between %s and %s")
        params.extend([start, end])

    sql = f"select * from {table_name} where {' and '.join(where)}"
    if table_has_column(conn, table_name, "time"):
        sql += " order by time, log_index"
    elif table_has_column(conn, table_name, "slot"):
        sql += " order by slot"
    if limit:
        sql += f" limit {int(limit)}"

    rows = 0
    with conn.cursor() as cur, output_path.open("w", encoding="utf-8") as f:
        cur.execute(sql, params)
        for row in cur:
            f.write(json.dumps(row, ensure_ascii=False, default=json_default))
            f.write("\n")
            rows += 1
    return rows


def extract_match(match_id, tables, output_root, dt=None, limit=None):
    output_dir = output_root / f"match_{match_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("tables", {})
    else:
        manifest = {
            "match_id": match_id,
            "database": os.environ.get("DB_NAME", "dota2_analysis"),
            "tables": {},
        }

    with connect() as conn:
        for table_name in tables:
            table_dts = detect_match_dts(conn, table_name, match_id)
            effective_dt = dt or (table_dts[0] if len(table_dts) == 1 else None)
            output_path = output_dir / f"{table_name}.jsonl"
            rows = write_query(conn, table_name, match_id, output_path, dt=effective_dt, limit=limit)
            manifest["tables"][table_name] = {
                "path": str(output_path.relative_to(output_root)),
                "rows": rows,
                "dt_filter": effective_dt,
                "available_dts": table_dts,
            }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path, manifest


def parse_args():
    parser = argparse.ArgumentParser(description="Extract one Dota2 match from StarRocks into local JSONL files.")
    parser.add_argument("match_id")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_ROOT), help="Output root directory. Default: raw_db/")
    parser.add_argument("--dt", help="Optional partition date, e.g. 2026-05-26.")
    parser.add_argument("--limit", type=int, help="Debug limit per table.")
    parser.add_argument("--include-actions", action="store_true", help="Also extract player action/order tables.")
    parser.add_argument("--tables", nargs="*", help="Override table list.")
    return parser.parse_args()


def main():
    args = parse_args()
    tables = args.tables or list(CORE_TABLES)
    if args.include_actions and not args.tables:
        tables.extend(ACTION_TABLES)
    manifest_path, manifest = extract_match(args.match_id, tables, Path(args.out), dt=args.dt, limit=args.limit)
    print(json.dumps({"manifest": str(manifest_path), "tables": manifest["tables"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
