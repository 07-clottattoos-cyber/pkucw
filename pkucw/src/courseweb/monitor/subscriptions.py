from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any

from .models import CourseUpdateEvent, DeliveryPlan, redact_grade_event, utc_now


DEFAULT_EVENT_TYPES = [
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
    "course.metadata_updated",
    "unknown.updated",
]


@dataclass(slots=True)
class Subscription:
    id: str
    enabled: bool = True
    mode: str = "realtime"
    event_types: list[str] = field(default_factory=lambda: list(DEFAULT_EVENT_TYPES))
    channels: list[str] = field(default_factory=lambda: ["sse"])
    include_sensitive_grade_content: bool = False
    target: str | None = None
    keywords: dict[str, list[str]] = field(default_factory=lambda: {"include": [], "exclude": []})
    quiet_hours: dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    digest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, subscription_id: str, data: dict[str, Any] | None) -> "Subscription":
        data = data or {}
        return cls(
            id=subscription_id,
            enabled=bool(data.get("enabled", True)),
            mode=str(data.get("mode", "realtime")),
            event_types=list(data.get("event_types") or DEFAULT_EVENT_TYPES),
            channels=list(data.get("channels") or ["sse"]),
            include_sensitive_grade_content=bool(data.get("include_sensitive_grade_content", False)),
            target=data.get("target") or data.get("url"),
            keywords=dict(data.get("keywords") or {"include": [], "exclude": []}),
            quiet_hours=dict(data.get("quiet_hours") or {"enabled": False}),
            digest=dict(data.get("digest") or {}),
        )


class SubscriptionEngine:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def match(self, event: CourseUpdateEvent) -> list[DeliveryPlan]:
        subscription = self._subscription_for_event(event)
        if not subscription.enabled:
            return []
        if event.event_type not in subscription.event_types:
            return []
        if not self._matches_keywords(event, subscription):
            return []

        sanitized, redacted = self.sanitize_event_for_subscription(event, subscription)
        if not self.should_deliver_now(sanitized, subscription):
            return []

        plans: list[DeliveryPlan] = []
        for channel in subscription.channels:
            plans.append(
                DeliveryPlan(
                    event_id=sanitized.event_id,
                    subscription_id=subscription.id,
                    course_id=sanitized.course_id,
                    channel=channel,
                    mode=subscription.mode,
                    target=subscription.target,
                    payload=sanitized.to_dict(),
                    deliver_at=utc_now(),
                    sensitive_redacted=redacted,
                )
            )
        return plans

    def sanitize_event_for_subscription(
        self,
        event: CourseUpdateEvent,
        subscription: Subscription,
    ) -> tuple[CourseUpdateEvent, bool]:
        if subscription.include_sensitive_grade_content:
            return copy.deepcopy(event), False
        return redact_grade_event(event)

    def should_deliver_now(self, event: CourseUpdateEvent, subscription: Subscription) -> bool:
        if subscription.mode == "manual":
            return False
        if subscription.mode == "digest":
            return False
        if subscription.mode == "hybrid" and event.severity not in {"urgent", "important"}:
            return False
        if self._in_quiet_hours(subscription) and event.severity != "urgent":
            return False
        return True

    def should_add_to_digest(self, event: CourseUpdateEvent, subscription: Subscription) -> bool:
        return subscription.mode in {"digest", "hybrid"} and event.severity != "urgent"

    def _subscription_for_event(self, event: CourseUpdateEvent) -> Subscription:
        courses = self.config.get("courses") or {}
        if event.course_id in courses:
            return Subscription.from_config(f"course:{event.course_id}", courses[event.course_id])
        return Subscription.from_config("default", self.config.get("default"))

    def _matches_keywords(self, event: CourseUpdateEvent, subscription: Subscription) -> bool:
        include = [item.lower() for item in subscription.keywords.get("include", []) if item]
        exclude = [item.lower() for item in subscription.keywords.get("exclude", []) if item]
        haystack = " ".join(
            [
                event.resource_title or "",
                event.summary or "",
                str(event.raw or ""),
                str(event.new_value or ""),
            ]
        ).lower()
        if include and not any(keyword in haystack for keyword in include):
            return False
        if exclude and any(keyword in haystack for keyword in exclude):
            return False
        return True

    def _in_quiet_hours(self, subscription: Subscription) -> bool:
        quiet = subscription.quiet_hours
        if not quiet.get("enabled"):
            return False
        start = _parse_time(quiet.get("start"))
        end = _parse_time(quiet.get("end"))
        if start is None or end is None:
            return False
        now = datetime.now(timezone.utc).time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end


def _parse_time(value: Any) -> time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return None
