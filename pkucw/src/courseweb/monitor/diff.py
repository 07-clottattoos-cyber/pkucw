from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    RESOURCE_ATTRS,
    BaseResourceSnapshot,
    CourseSnapshot,
    CourseUpdateEvent,
    event_id_for,
    stable_hash,
    utc_now,
)


CREATED_TYPES = {
    "announcement": "announcement.created",
    "assignment": "assignment.created",
    "content": "content.created",
    "recording": "recording.created",
}
UPDATED_TYPES = {
    "announcement": "announcement.updated",
    "assignment": "assignment.updated",
    "content": "content.updated",
    "recording": "recording.updated",
    "grade": "grade.updated",
}


@dataclass(slots=True)
class DiffOptions:
    first_scan_notify: bool = False
    include_sensitive_grade_content: bool = False


class DiffEngine:
    def diff(
        self,
        old_snapshot: CourseSnapshot | None,
        new_snapshot: CourseSnapshot,
        *,
        options: DiffOptions | None = None,
    ) -> list[CourseUpdateEvent]:
        options = options or DiffOptions()
        if old_snapshot is None and not options.first_scan_notify:
            return []

        events: list[CourseUpdateEvent] = []
        for resource_type in RESOURCE_ATTRS:
            old_items = [] if old_snapshot is None else getattr(old_snapshot, RESOURCE_ATTRS[resource_type])
            new_items = getattr(new_snapshot, RESOURCE_ATTRS[resource_type])
            events.extend(
                self._diff_resources(
                    old_items,
                    new_items,
                    resource_type=resource_type,
                    snapshot=new_snapshot,
                    options=options,
                    old_snapshot_missing=old_snapshot is None,
                )
            )

        if old_snapshot is not None and self._metadata_hash(old_snapshot) != self._metadata_hash(new_snapshot):
            events.append(
                self._event(
                    snapshot=new_snapshot,
                    resource_type="course",
                    event_type="course.metadata_updated",
                    resource=None,
                    old_hash=self._metadata_hash(old_snapshot),
                    new_hash=self._metadata_hash(new_snapshot),
                    old_value=old_snapshot.raw_meta,
                    new_value=new_snapshot.raw_meta,
                    changed_fields=["raw_meta"],
                    severity="normal",
                    summary=f"{new_snapshot.course_name} 课程信息已更新。",
                )
            )
        return events

    def _diff_resources(
        self,
        old_items: list[BaseResourceSnapshot],
        new_items: list[BaseResourceSnapshot],
        *,
        resource_type: str,
        snapshot: CourseSnapshot,
        options: DiffOptions,
        old_snapshot_missing: bool,
    ) -> list[CourseUpdateEvent]:
        events: list[CourseUpdateEvent] = []
        old_by_id = {item.stable_id(): item for item in old_items}
        for new_item in new_items:
            key = new_item.stable_id()
            old_item = old_by_id.get(key)
            if old_item is None:
                if resource_type in CREATED_TYPES:
                    events.append(
                        self._event(
                            snapshot=snapshot,
                            resource_type=resource_type,
                            event_type=CREATED_TYPES[resource_type],
                            resource=new_item,
                            old_hash=None,
                            new_hash=new_item.ensure_hash(),
                            old_value=None,
                            new_value=new_item.to_dict(),
                            changed_fields=["created"],
                            severity="normal",
                            summary=_created_summary(snapshot.course_name, resource_type, new_item.title),
                        )
                    )
                elif old_snapshot_missing and resource_type == "grade" and options.first_scan_notify:
                    events.append(self._grade_event(snapshot, None, new_item, options=options))
                continue

            if resource_type == "assignment" and getattr(old_item, "due_at", None) != getattr(new_item, "due_at", None):
                events.append(self._deadline_event(snapshot, old_item, new_item))
                continue

            if old_item.ensure_hash() != new_item.ensure_hash():
                if resource_type == "grade":
                    events.append(self._grade_event(snapshot, old_item, new_item, options=options))
                else:
                    events.append(
                        self._event(
                            snapshot=snapshot,
                            resource_type=resource_type,
                            event_type=UPDATED_TYPES[resource_type],
                            resource=new_item,
                            old_hash=old_item.ensure_hash(),
                            new_hash=new_item.ensure_hash(),
                            old_value=old_item.to_dict(),
                            new_value=new_item.to_dict(),
                            changed_fields=_changed_fields(old_item.to_dict(), new_item.to_dict()),
                            severity="normal",
                            summary=_updated_summary(snapshot.course_name, resource_type, new_item.title),
                        )
                    )
        return events

    def _deadline_event(
        self,
        snapshot: CourseSnapshot,
        old_item: BaseResourceSnapshot,
        new_item: BaseResourceSnapshot,
    ) -> CourseUpdateEvent:
        old_due = getattr(old_item, "due_at", None)
        new_due = getattr(new_item, "due_at", None)
        severity = "urgent" if new_due and not old_due else "important"
        title = new_item.title or "未命名作业"
        summary = f"{snapshot.course_name} 作业「{title}」截止时间从 {old_due or '未设置'} 改为 {new_due or '未设置'}。"
        return self._event(
            snapshot=snapshot,
            resource_type="assignment",
            event_type="assignment.deadline_changed",
            resource=new_item,
            old_hash=old_item.ensure_hash(),
            new_hash=new_item.ensure_hash(),
            old_value=old_item.to_dict(),
            new_value=new_item.to_dict(),
            changed_fields=["due_at"],
            severity=severity,
            summary=summary,
        )

    def _grade_event(
        self,
        snapshot: CourseSnapshot,
        old_item: BaseResourceSnapshot | None,
        new_item: BaseResourceSnapshot,
        *,
        options: DiffOptions,
    ) -> CourseUpdateEvent:
        new_value = new_item.to_dict()
        old_value = old_item.to_dict() if old_item else None
        if not options.include_sensitive_grade_content:
            for value in (old_value, new_value):
                if isinstance(value, dict):
                    value.pop("score", None)
                    value.pop("max_score", None)
                    value["has_sensitive_change"] = True
        return self._event(
            snapshot=snapshot,
            resource_type="grade",
            event_type="grade.updated",
            resource=new_item,
            old_hash=old_item.ensure_hash() if old_item else None,
            new_hash=new_item.ensure_hash(),
            old_value=old_value,
            new_value=new_value,
            changed_fields=_changed_fields(old_item.to_dict(), new_item.to_dict()) if old_item else ["created"],
            severity="important",
            summary=f"{snapshot.course_name} 成绩已更新。",
        )

    def _event(
        self,
        *,
        snapshot: CourseSnapshot,
        resource_type: str,
        event_type: str,
        resource: BaseResourceSnapshot | None,
        old_hash: str | None,
        new_hash: str | None,
        old_value: dict[str, Any] | None,
        new_value: dict[str, Any] | None,
        changed_fields: list[str],
        severity: str,
        summary: str,
    ) -> CourseUpdateEvent:
        resource_id = resource.stable_id() if resource else snapshot.course_id
        return CourseUpdateEvent(
            event_id=event_id_for(
                course_id=snapshot.course_id,
                resource_type=resource_type,
                resource_id=resource_id,
                event_type=event_type,
                new_hash=new_hash,
            ),
            event_type=event_type,
            course_id=snapshot.course_id,
            course_name=snapshot.course_name,
            semester=snapshot.semester,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_title=resource.title if resource else snapshot.course_name,
            source_url=resource.url if resource else None,
            old_hash=old_hash,
            new_hash=new_hash,
            old_value=old_value,
            new_value=new_value,
            changed_fields=changed_fields,
            detected_at=utc_now(),
            severity=severity,
            summary=summary,
            raw=None,
        )

    def _metadata_hash(self, snapshot: CourseSnapshot) -> str:
        return stable_hash(
            {
                "course_id": snapshot.course_id,
                "course_name": snapshot.course_name,
                "semester": snapshot.semester,
                "raw_meta": snapshot.raw_meta,
            }
        )


def _changed_fields(old_value: dict[str, Any], new_value: dict[str, Any]) -> list[str]:
    keys = sorted(set(old_value) | set(new_value))
    return [key for key in keys if old_value.get(key) != new_value.get(key)]


def _created_summary(course_name: str, resource_type: str, title: str | None) -> str:
    title = title or "未命名资源"
    labels = {
        "announcement": "新增公告",
        "assignment": "新增作业",
        "content": "新增课程内容",
        "recording": "新增回放",
    }
    return f"{course_name} {labels[resource_type]}：{title}。"


def _updated_summary(course_name: str, resource_type: str, title: str | None) -> str:
    title = title or "未命名资源"
    labels = {
        "announcement": "公告已更新",
        "assignment": "作业已更新",
        "content": "课程内容已更新",
        "recording": "回放已更新",
    }
    return f"{course_name} {labels[resource_type]}：{title}。"
