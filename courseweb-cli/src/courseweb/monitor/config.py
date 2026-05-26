from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from ..state import courseweb_home


DEFAULT_CONFIG: dict[str, Any] = {
    "monitor": {
        "enabled": True,
        "interval_seconds": 300,
        "jitter_seconds": 30,
        "first_scan_notify": False,
        "per_resource_interval": {
            "grades": 300,
            "assignments": 300,
            "contents": 900,
            "recordings": 900,
            "announcements": 600,
        },
    },
    "subscriptions": {
        "default": {
            "enabled": True,
            "mode": "realtime",
            "event_types": [
                "grade.updated",
                "assignment.created",
                "assignment.updated",
                "assignment.deadline_changed",
                "content.created",
                "content.updated",
                "recording.created",
                "recording.updated",
                "announcement.created",
                "announcement.updated",
            ],
            "channels": ["sse"],
            "include_sensitive_grade_content": False,
            "quiet_hours": {"enabled": False, "start": "23:30", "end": "08:30"},
        },
        "courses": {},
    },
    "agent": {
        "host": "127.0.0.1",
        "port": 8765,
        "token": None,
        "require_token": True,
        "allow_modify_subscriptions": False,
    },
    "notifiers": {"webhook": {}, "hermes": {}},
}


def config_path() -> Path:
    return courseweb_home() / "config.json"


def database_path() -> Path:
    raw = os.environ.get("COURSEWEB_MONITOR_DB")
    if raw:
        return Path(raw).expanduser().resolve()
    return courseweb_home() / "monitor.sqlite3"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(config)
        return config
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        loaded = {}
    return _merge(DEFAULT_CONFIG, loaded)


def save_config(config: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def ensure_agent_token(config: dict[str, Any] | None = None) -> str:
    config = config or load_config()
    agent = config.setdefault("agent", {})
    token = agent.get("token")
    if not token:
        token = secrets.token_urlsafe(32)
        agent["token"] = token
        agent.setdefault("require_token", True)
        save_config(config)
    return str(token)


def update_course_subscription(course_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    courses = config.setdefault("subscriptions", {}).setdefault("courses", {})
    current = dict(courses.get(course_id) or {})
    current.update({key: value for key, value in patch.items() if value is not None})
    courses[course_id] = current
    save_config(config)
    return current


def _merge(default: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(default))
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result
