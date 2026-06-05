from __future__ import annotations

import json
import os
from dataclasses import fields
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path

from .models import AccountRecord, SessionState


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def courseweb_home() -> Path:
    raw = os.environ.get("COURSEWEB_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".courseweb"


def session_path() -> Path:
    return courseweb_home() / "session.json"


def storage_state_path() -> Path:
    return courseweb_home() / "storage_state.json"


def accounts_path() -> Path:
    return courseweb_home() / "accounts.json"


def ensure_home() -> Path:
    home = courseweb_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def load_session() -> SessionState:
    path = session_path()
    if not path.exists():
        return SessionState()

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return SessionState()

    try:
        data = json.loads(raw)
    except JSONDecodeError:
        return SessionState()

    allowed = {item.name for item in fields(SessionState)}
    filtered = {key: value for key, value in data.items() if key in allowed}
    return SessionState(**filtered)


def save_session(state: SessionState) -> Path:
    ensure_home()
    path = session_path()
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_accounts() -> list[AccountRecord]:
    path = accounts_path()
    if not path.exists():
        return []

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    try:
        data = json.loads(raw)
    except JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    allowed = {item.name for item in fields(AccountRecord)}
    accounts: list[AccountRecord] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        filtered = {key: value for key, value in item.items() if key in allowed}
        try:
            accounts.append(AccountRecord(**filtered))
        except TypeError:
            continue
    return accounts


def save_accounts(accounts: list[AccountRecord]) -> Path:
    ensure_home()
    path = accounts_path()
    path.write_text(
        json.dumps([account.to_dict() for account in accounts], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def clear_session() -> bool:
    removed = False
    for path in (session_path(), storage_state_path()):
        if path.exists():
            path.unlink()
            removed = True
    return removed
