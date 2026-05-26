from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import CourseSnapshot, CourseUpdateEvent, RESOURCE_ATTRS, stable_json, utc_now


SCHEMA = """
create table if not exists course_snapshots (
  id integer primary key,
  course_id text not null,
  semester text,
  fetched_at text not null,
  snapshot_hash text not null,
  snapshot_json text not null
);
create index if not exists idx_course_snapshots_course on course_snapshots(course_id, id);
create table if not exists resource_states (
  course_id text not null,
  resource_type text not null,
  resource_id text not null,
  resource_hash text not null,
  resource_json text not null,
  first_seen_at text not null,
  last_seen_at text not null,
  primary key(course_id, resource_type, resource_id)
);
create table if not exists update_events (
  event_id text primary key,
  event_type text not null,
  course_id text not null,
  course_name text not null,
  resource_type text not null,
  resource_id text,
  resource_title text,
  severity text not null,
  summary text not null,
  detected_at text not null,
  event_json text not null,
  acknowledged_at text null
);
create index if not exists idx_update_events_detected on update_events(detected_at, event_id);
create table if not exists subscriptions (
  id text primary key,
  name text not null,
  scope text not null,
  course_id text null,
  enabled integer not null,
  config_json text not null,
  created_at text not null,
  updated_at text not null
);
create table if not exists notification_deliveries (
  id integer primary key,
  event_id text not null,
  subscription_id text not null,
  channel text not null,
  target text,
  status text not null,
  attempts integer not null,
  next_retry_at text null,
  last_error text null,
  delivered_at text null
);
create table if not exists digest_items (
  id integer primary key,
  event_id text not null,
  subscription_id text not null,
  digest_key text not null,
  added_at text not null,
  sent_at text null
);
create table if not exists monitor_status (
  course_id text primary key,
  last_success_at text null,
  last_error text null,
  updated_at text not null
);
"""


class MonitorStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def save_snapshot(self, snapshot: CourseSnapshot) -> None:
        now = snapshot.fetched_at.isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                insert into course_snapshots(course_id, semester, fetched_at, snapshot_hash, snapshot_json)
                values (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.course_id,
                    snapshot.semester,
                    now,
                    snapshot.snapshot_hash(),
                    json.dumps(snapshot.to_dict(), ensure_ascii=False),
                ),
            )
            for resource_type, attr in RESOURCE_ATTRS.items():
                for item in getattr(snapshot, attr):
                    resource_id = item.stable_id()
                    resource_hash = item.ensure_hash()
                    resource_json = json.dumps(item.to_dict(), ensure_ascii=False)
                    conn.execute(
                        """
                        insert into resource_states(
                          course_id, resource_type, resource_id, resource_hash, resource_json,
                          first_seen_at, last_seen_at
                        ) values (?, ?, ?, ?, ?, ?, ?)
                        on conflict(course_id, resource_type, resource_id) do update set
                          resource_hash=excluded.resource_hash,
                          resource_json=excluded.resource_json,
                          last_seen_at=excluded.last_seen_at
                        """,
                        (
                            snapshot.course_id,
                            resource_type,
                            resource_id,
                            resource_hash,
                            resource_json,
                            now,
                            now,
                        ),
                    )

    def latest_snapshot(self, course_id: str) -> CourseSnapshot | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select snapshot_json from course_snapshots
                where course_id = ?
                order by id desc
                limit 1
                """,
                (course_id,),
            ).fetchone()
        if row is None:
            return None
        from .snapshot import snapshot_from_dict

        return snapshot_from_dict(json.loads(row["snapshot_json"]))

    def latest_snapshots(self) -> list[CourseSnapshot]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select s.snapshot_json
                from course_snapshots s
                join (
                  select course_id, max(id) as max_id from course_snapshots group by course_id
                ) latest on latest.max_id = s.id
                order by s.course_id
                """
            ).fetchall()
        from .snapshot import snapshot_from_dict

        return [snapshot_from_dict(json.loads(row["snapshot_json"])) for row in rows]

    def add_events(self, events: list[CourseUpdateEvent]) -> int:
        inserted = 0
        with self.connect() as conn:
            for event in events:
                result = conn.execute(
                    """
                    insert or ignore into update_events(
                      event_id, event_type, course_id, course_name, resource_type,
                      resource_id, resource_title, severity, summary, detected_at, event_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.course_id,
                        event.course_name,
                        event.resource_type,
                        event.resource_id,
                        event.resource_title,
                        event.severity,
                        event.summary,
                        event.detected_at.isoformat(),
                        json.dumps(event.to_dict(), ensure_ascii=False),
                    ),
                )
                inserted += result.rowcount
        return inserted

    def list_events(
        self,
        *,
        course_id: str | None = None,
        event_types: list[str] | None = None,
        since: str | None = None,
        after_event_id: str | None = None,
        limit: int = 50,
    ) -> list[CourseUpdateEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if course_id:
            clauses.append("course_id = ?")
            params.append(course_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type in ({placeholders})")
            params.extend(event_types)
        if since:
            clauses.append("detected_at >= ?")
            params.append(since)
        if after_event_id:
            clauses.append(
                "rowid > coalesce((select rowid from update_events where event_id = ?), 0)"
            )
            params.append(after_event_id)
        where = " where " + " and ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                select event_json from update_events
                {where}
                order by detected_at asc, event_id asc
                limit ?
                """,
                params,
            ).fetchall()
        return [CourseUpdateEvent.from_dict(json.loads(row["event_json"])) for row in rows]

    def get_event(self, event_id: str) -> CourseUpdateEvent | None:
        with self.connect() as conn:
            row = conn.execute(
                "select event_json from update_events where event_id = ?",
                (event_id,),
            ).fetchone()
        return CourseUpdateEvent.from_dict(json.loads(row["event_json"])) if row else None

    def acknowledge_event(self, event_id: str) -> bool:
        with self.connect() as conn:
            result = conn.execute(
                "update update_events set acknowledged_at = ? where event_id = ?",
                (utc_now().isoformat(), event_id),
            )
        return result.rowcount > 0

    def upsert_subscription(
        self,
        subscription_id: str,
        *,
        name: str,
        scope: str,
        course_id: str | None,
        enabled: bool,
        config: dict[str, Any],
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                insert into subscriptions(id, name, scope, course_id, enabled, config_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                  name=excluded.name,
                  scope=excluded.scope,
                  course_id=excluded.course_id,
                  enabled=excluded.enabled,
                  config_json=excluded.config_json,
                  updated_at=excluded.updated_at
                """,
                (
                    subscription_id,
                    name,
                    scope,
                    course_id,
                    1 if enabled else 0,
                    json.dumps(config, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )

    def list_subscriptions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from subscriptions order by scope, course_id, id").fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "scope": row["scope"],
                "course_id": row["course_id"],
                "enabled": bool(row["enabled"]),
                "config": json.loads(row["config_json"]),
            }
            for row in rows
        ]

    def add_delivery(self, plan: dict[str, Any], *, status: str = "pending", error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into notification_deliveries(
                  event_id, subscription_id, channel, target, status, attempts,
                  next_retry_at, last_error, delivered_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["event_id"],
                    plan["subscription_id"],
                    plan["channel"],
                    plan.get("target"),
                    status,
                    0,
                    None,
                    error,
                    utc_now().isoformat() if status == "delivered" else None,
                ),
            )

    def set_course_status(self, course_id: str, *, ok: bool, error: str | None = None) -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                insert into monitor_status(course_id, last_success_at, last_error, updated_at)
                values (?, ?, ?, ?)
                on conflict(course_id) do update set
                  last_success_at=coalesce(excluded.last_success_at, monitor_status.last_success_at),
                  last_error=excluded.last_error,
                  updated_at=excluded.updated_at
                """,
                (course_id, now if ok else None, None if ok else error, now),
            )

    def status(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from monitor_status order by course_id").fetchall()
        return [dict(row) for row in rows]


def scrub_secrets(value: Any) -> Any:
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.lower() in {"token", "secret", "webhook_secret", "authorization"}:
                result[key] = "<redacted>"
            else:
                result[key] = scrub_secrets(item)
        return result
    return value
