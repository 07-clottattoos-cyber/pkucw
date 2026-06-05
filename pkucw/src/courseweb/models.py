from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SessionState:
    configured: bool = False
    auth_mode: str | None = None
    cookie_jar: str | None = None
    storage_state: str | None = None
    browser_profile: str | None = None
    login_url: str | None = None
    user_display: str | None = None
    note: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_verified_at: str | None = None
    authenticated: bool = False
    account_username: str | None = None
    account_label: str | None = None
    active_course_id: str | None = None
    active_course_title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AccountRecord:
    username: str
    label: str | None = None
    is_default: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    last_used_at: str | None = None
    last_login_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CommandResult:
    ok: bool
    message: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "payload": self.payload,
        }
