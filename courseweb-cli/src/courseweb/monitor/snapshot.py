from __future__ import annotations

from datetime import datetime
from typing import Any

from ..announcements import scrape_announcements
from ..assignments import scrape_assignments
from ..contents import scrape_contents
from ..courses import CourseRecord, scrape_courses
from ..grades import scrape_grades
from ..recordings import scrape_recordings
from ..state import load_session
from .models import (
    AnnouncementSnapshot,
    AssignmentSnapshot,
    ContentSnapshot,
    CourseSnapshot,
    GradeSnapshot,
    RecordingSnapshot,
    canonical_url,
    parse_datetime,
    stable_hash,
    utc_now,
)


def collect_snapshots(
    *,
    storage_state_path: str | None = None,
    course_ids: list[str] | None = None,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> list[CourseSnapshot]:
    if storage_state_path is None:
        session = load_session()
        if not session.storage_state:
            raise RuntimeError("没有可用的浏览器会话，请先运行 `pkucw login`。")
        storage_state_path = session.storage_state

    courses = scrape_courses(storage_state_path=storage_state_path, headless=headless, timeout_ms=timeout_ms)
    if course_ids:
        wanted = set(course_ids)
        courses = [course for course in courses if course.id in wanted or course.name in wanted or course.title in wanted]
    return [
        collect_course_snapshot(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        for course in courses
    ]


def collect_course_snapshot(
    *,
    storage_state_path: str,
    course: CourseRecord,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> CourseSnapshot:
    course_id = course.id or course.title
    announcements = []
    assignments = []
    contents = []
    recordings = []
    grades = []

    try:
        _, announcement_details = scrape_announcements(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        announcements = [announcement_from_scrape(item.to_dict()) for item in announcement_details]
    except Exception:
        announcements = []

    try:
        _, assignment_items = scrape_assignments(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        assignments = [assignment_from_scrape(item.to_dict()) for item in assignment_items]
    except Exception:
        assignments = []

    try:
        _, content_items = scrape_contents(
            storage_state_path=storage_state_path,
            course=course,
            recursive=False,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        contents = [content_from_scrape(item.to_dict()) for item in content_items]
    except Exception:
        contents = []

    try:
        _, recording_items = scrape_recordings(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        recordings = [recording_from_scrape(item.to_dict()) for item in recording_items]
    except Exception:
        recordings = []

    try:
        _, grade_items = scrape_grades(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        grades = [grade_from_scrape(item.to_dict()) for item in grade_items]
    except Exception:
        grades = []

    return CourseSnapshot(
        course_id=course_id,
        course_name=course.name,
        semester=course.term,
        fetched_at=utc_now(),
        announcements=announcements,
        assignments=assignments,
        grades=grades,
        contents=contents,
        recordings=recordings,
        raw_meta=course.to_dict(),
    )


def snapshot_from_dict(data: dict[str, Any]) -> CourseSnapshot:
    return CourseSnapshot(
        course_id=data["course_id"],
        course_name=data["course_name"],
        semester=data.get("semester"),
        fetched_at=parse_datetime(data.get("fetched_at")) or utc_now(),
        announcements=[AnnouncementSnapshot(**item) for item in data.get("announcements", [])],
        assignments=[AssignmentSnapshot(**item) for item in data.get("assignments", [])],
        grades=[GradeSnapshot(**item) for item in data.get("grades", [])],
        contents=[ContentSnapshot(**item) for item in data.get("contents", [])],
        recordings=[RecordingSnapshot(**item) for item in data.get("recordings", [])],
        raw_meta=data.get("raw_meta"),
    )


def snapshot_from_fixture(data: dict[str, Any]) -> CourseSnapshot:
    payload = dict(data)
    payload.setdefault("fetched_at", utc_now().isoformat())
    return snapshot_from_dict(payload)


def announcement_from_scrape(data: dict[str, Any]) -> AnnouncementSnapshot:
    item = data.get("announcement") or data
    raw = data
    return _with_hash(
        AnnouncementSnapshot(
            id=item.get("id"),
            title=item.get("title"),
            published_at=item.get("published_at"),
            url=None,
            raw=raw,
        )
    )


def assignment_from_scrape(data: dict[str, Any]) -> AssignmentSnapshot:
    item = data.get("assignment") or data
    return _with_hash(
        AssignmentSnapshot(
            id=item.get("id"),
            title=item.get("title"),
            due_at=data.get("due_at") or item.get("due_at"),
            status=item.get("status") or item.get("type"),
            url=canonical_url(item.get("url")),
            raw=data,
        )
    )


def grade_from_scrape(data: dict[str, Any]) -> GradeSnapshot:
    item = data.get("grade") or data
    return _with_hash(
        GradeSnapshot(
            id=item.get("id"),
            title=item.get("title"),
            score=item.get("score"),
            max_score=item.get("max_score"),
            published_at=item.get("published_at"),
            url=canonical_url(item.get("url")),
            raw=data,
        )
    )


def content_from_scrape(data: dict[str, Any]) -> ContentSnapshot:
    item = data.get("content") or data
    return _with_hash(
        ContentSnapshot(
            id=item.get("id"),
            title=item.get("title"),
            kind=item.get("type") or item.get("kind"),
            updated_at=item.get("updated_at"),
            url=canonical_url(item.get("url")),
            file_name=item.get("file_name") or item.get("title"),
            size=item.get("size"),
            raw=data,
        )
    )


def recording_from_scrape(data: dict[str, Any]) -> RecordingSnapshot:
    item = data.get("recording") or data
    return _with_hash(
        RecordingSnapshot(
            id=item.get("id"),
            title=item.get("title"),
            available_at=item.get("recorded_at") or item.get("available_at"),
            duration=item.get("duration_seconds") or item.get("duration"),
            url=canonical_url(item.get("watch_url") or item.get("url")),
            raw=data,
        )
    )


def _with_hash(item):
    item.content_hash = stable_hash(item.to_dict())
    return item
