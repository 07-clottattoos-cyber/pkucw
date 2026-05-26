from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


VOLATILE_KEYS = {
    "csrf",
    "csrf_token",
    "token",
    "nonce",
    "random",
    "timestamp",
    "current_time",
    "visited_at",
    "accessed_at",
    "temp_id",
    "session",
    "cookie",
}
VOLATILE_QUERY_KEYS = {"csrf", "token", "nonce", "random", "_", "t", "ts", "timestamp", "session"}
WS_RE = re.compile(r"\s+")
T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    for candidate in (normalized, normalized.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def datetime_to_json(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in VOLATILE_QUERY_KEYS
    ]
    query.sort()
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urlencode(query, doseq=True),
            "",
        )
    )


def normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return datetime_to_json(value)
    if isinstance(value, str):
        stripped = WS_RE.sub(" ", value).strip()
        if stripped.startswith(("http://", "https://")):
            return canonical_url(stripped)
        return stripped
    if isinstance(value, list):
        normalized = [normalize_value(item) for item in value]
        return sorted(normalized, key=lambda item: stable_json(item))
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in VOLATILE_KEYS:
                continue
            result[key] = normalize_value(item)
        return {key: result[key] for key in sorted(result)}
    return value


def stable_json(value: Any) -> str:
    return json.dumps(normalize_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def event_id_for(
    *,
    course_id: str,
    resource_type: str,
    resource_id: str | None,
    event_type: str,
    new_hash: str | None,
) -> str:
    return stable_hash(
        {
            "course_id": course_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "event_type": event_type,
            "new_hash": new_hash,
        }
    )


@dataclass(slots=True)
class BaseResourceSnapshot:
    id: str | None
    title: str | None
    url: str | None = None
    content_hash: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)

    def stable_id(self) -> str:
        return str(self.id or self.url or self.title or stable_hash(self.to_dict()))

    def ensure_hash(self) -> str:
        if self.content_hash:
            return self.content_hash
        return stable_hash(self.to_dict())


@dataclass(slots=True)
class AnnouncementSnapshot(BaseResourceSnapshot):
    published_at: str | None = None


@dataclass(slots=True)
class AssignmentSnapshot(BaseResourceSnapshot):
    due_at: str | None = None
    status: str | None = None


@dataclass(slots=True)
class GradeSnapshot(BaseResourceSnapshot):
    score: str | int | float | None = None
    max_score: str | int | float | None = None
    published_at: str | None = None


@dataclass(slots=True)
class ContentSnapshot(BaseResourceSnapshot):
    kind: str | None = None
    updated_at: str | None = None
    file_name: str | None = None
    size: str | int | None = None


@dataclass(slots=True)
class RecordingSnapshot(BaseResourceSnapshot):
    available_at: str | None = None
    duration: str | int | float | None = None


@dataclass(slots=True)
class CourseSnapshot:
    course_id: str
    course_name: str
    semester: str | None
    fetched_at: datetime
    announcements: list[AnnouncementSnapshot] = field(default_factory=list)
    assignments: list[AssignmentSnapshot] = field(default_factory=list)
    grades: list[GradeSnapshot] = field(default_factory=list)
    contents: list[ContentSnapshot] = field(default_factory=list)
    recordings: list[RecordingSnapshot] = field(default_factory=list)
    raw_meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)

    def snapshot_hash(self) -> str:
        data = self.to_dict()
        data.pop("fetched_at", None)
        return stable_hash(data)


@dataclass(slots=True)
class CourseUpdateEvent:
    event_id: str
    event_type: str
    course_id: str
    course_name: str
    semester: str | None
    resource_type: str
    resource_id: str | None
    resource_title: str | None
    source_url: str | None
    old_hash: str | None
    new_hash: str | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    changed_fields: list[str]
    detected_at: datetime
    severity: str
    summary: str
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CourseUpdateEvent":
        payload = dict(data)
        payload["detected_at"] = parse_datetime(payload.get("detected_at")) or utc_now()
        return cls(**{field.name: payload.get(field.name) for field in fields(cls)})


@dataclass(slots=True)
class DeliveryPlan:
    event_id: str
    subscription_id: str
    course_id: str
    channel: str
    mode: str
    target: str | None
    payload: dict[str, Any]
    deliver_at: datetime | None
    sensitive_redacted: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    def convert(item: Any) -> Any:
        if isinstance(item, datetime):
            return datetime_to_json(item)
        if hasattr(item, "__dataclass_fields__"):
            return {key: convert(val) for key, val in asdict(item).items()}
        if isinstance(item, list):
            return [convert(part) for part in item]
        if isinstance(item, dict):
            return {key: convert(val) for key, val in item.items()}
        return item

    return convert(value)


def redact_grade_event(event: CourseUpdateEvent) -> tuple[CourseUpdateEvent, bool]:
    if event.resource_type != "grade" and event.event_type != "grade.updated":
        return event, False
    sanitized = copy.deepcopy(event)
    for attr in ("old_value", "new_value"):
        value = getattr(sanitized, attr)
        if isinstance(value, dict):
            value.pop("score", None)
            value.pop("max_score", None)
            value["has_sensitive_change"] = True
    sanitized.summary = f"{event.course_name} 成绩已更新。"
    return sanitized, True


RESOURCE_ATTRS: dict[str, str] = {
    "announcement": "announcements",
    "assignment": "assignments",
    "grade": "grades",
    "content": "contents",
    "recording": "recordings",
}
