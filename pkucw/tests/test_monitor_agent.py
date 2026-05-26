from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timezone

import pytest
from fastapi.testclient import TestClient

from courseweb.agent.api_server import create_app
from courseweb.agent.cli_bridge import build_cli_command_specs, run_generic_cli
from courseweb.agent.mcp_server import CoursewebMcpTools
from courseweb.monitor.diff import DiffEngine
from courseweb.monitor.models import CourseUpdateEvent, DeliveryPlan, event_id_for, utc_now
from courseweb.monitor.notifier import _webhook_payload
from courseweb.monitor.service import MonitorService
from courseweb.monitor.snapshot import snapshot_from_fixture
from courseweb.monitor.store import MonitorStore
from courseweb.monitor.subscriptions import SubscriptionEngine


def fixture_snapshot(assignments=None, grades=None, contents=None, recordings=None, announcements=None):
    return snapshot_from_fixture(
        {
            "course_id": "COURSE_A",
            "course_name": "量子力学",
            "semester": "2025-2026学年第2学期",
            "assignments": assignments or [],
            "grades": grades or [],
            "contents": contents or [],
            "recordings": recordings or [],
            "announcements": announcements or [],
            "raw_meta": {"teacher": "张三"},
        }
    )


def test_first_scan_creates_baseline_without_events(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    snapshot = fixture_snapshot(assignments=[{"id": "a1", "title": "作业 1", "url": None}])
    service = MonitorService(store=store, config={}, snapshot_provider=lambda: [snapshot])

    result = service.scan(notify=False)

    assert result["events"] == 0
    assert store.latest_snapshot("COURSE_A") is not None


def test_assignment_created_event():
    old = fixture_snapshot(assignments=[{"id": "a1", "title": "作业 1", "url": None}])
    new = fixture_snapshot(
        assignments=[
            {"id": "a1", "title": "作业 1", "url": None},
            {"id": "a2", "title": "第 5 次作业", "url": None},
        ]
    )

    events = DiffEngine().diff(old, new)

    assert [event.event_type for event in events] == ["assignment.created"]
    assert "第 5 次作业" in events[0].summary


def test_assignment_deadline_changed_event():
    old = fixture_snapshot(assignments=[{"id": "a1", "title": "Project 2", "due_at": "2026-05-28", "url": None}])
    new = fixture_snapshot(assignments=[{"id": "a1", "title": "Project 2", "due_at": "2026-05-30", "url": None}])

    events = DiffEngine().diff(old, new)

    assert events[0].event_type == "assignment.deadline_changed"
    assert events[0].changed_fields == ["due_at"]
    assert "2026-05-30" in events[0].summary


def test_grade_update_redacted_by_default():
    old = fixture_snapshot(grades=[{"id": "g1", "title": "期中", "score": "88", "max_score": "100", "url": None}])
    new = fixture_snapshot(grades=[{"id": "g1", "title": "期中", "score": "92", "max_score": "100", "url": None}])

    events = DiffEngine().diff(old, new)

    assert events[0].event_type == "grade.updated"
    assert events[0].new_value["has_sensitive_change"] is True
    assert "score" not in events[0].new_value
    assert "92" not in events[0].summary


def test_course_specific_subscription_overrides_default():
    event = make_event("assignment.created", course_id="COURSE_A")
    engine = SubscriptionEngine(
        {
            "default": {"enabled": True, "event_types": ["grade.updated"], "channels": ["console"]},
            "courses": {
                "COURSE_A": {
                    "enabled": True,
                    "event_types": ["assignment.created"],
                    "channels": ["sse", "hermes"],
                }
            },
        }
    )

    plans = engine.match(event)

    assert [plan.channel for plan in plans] == ["sse", "hermes"]


def test_muted_course_no_delivery():
    event = make_event("assignment.created", course_id="COURSE_A")
    engine = SubscriptionEngine({"courses": {"COURSE_A": {"enabled": False}}})

    assert engine.match(event) == []


def test_course_digest_subscription():
    event = make_event("content.created", course_id="COURSE_A")
    engine = SubscriptionEngine(
        {
            "courses": {
                "COURSE_A": {
                    "enabled": True,
                    "mode": "digest",
                    "event_types": ["content.created"],
                    "channels": ["sse"],
                }
            }
        }
    )

    assert engine.match(event) == []


def test_keyword_include_filter():
    event = make_event("content.created", title="第 10 讲课件")
    engine = SubscriptionEngine({"default": {"event_types": ["content.created"], "keywords": {"include": ["课件"], "exclude": []}}})

    assert len(engine.match(event)) == 1


def test_keyword_exclude_filter():
    event = make_event("content.created", title="考试答案")
    engine = SubscriptionEngine({"default": {"event_types": ["content.created"], "keywords": {"include": [], "exclude": ["答案"]}}})

    assert engine.match(event) == []


def test_webhook_signature():
    event = make_event("assignment.deadline_changed").to_dict()
    body = json.dumps(_webhook_payload(event), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert signature
    assert hmac.compare_digest(signature, hmac.new(b"secret", body, hashlib.sha256).hexdigest())


def test_sse_replay_last_event_id(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    first = make_event("assignment.created", title="作业 1")
    second = make_event("content.created", title="课件 1")
    store.add_events([first, second])
    app = create_app(store=store, config={"agent": {"require_token": False}})
    client = TestClient(app)

    with client.stream("GET", "/events?replay_only=true", headers={"Last-Event-ID": first.event_id}) as response:
        chunk = next(response.iter_text())

    assert response.status_code == 200
    assert second.event_id in chunk
    assert first.event_id not in chunk


def test_mcp_list_recent_updates(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    event = make_event("assignment.created")
    store.add_events([event])
    tools = CoursewebMcpTools(store=store, config={})

    result = tools.list_recent_updates(limit=10)

    assert result["updates"][0]["event_id"] == event.event_id


def test_agent_cannot_modify_subscription_by_default(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    tools = CoursewebMcpTools(store=store, config={"agent": {"allow_modify_subscriptions": False}})

    result = tools.update_course_subscription("COURSE_A", enabled=False)

    assert result["ok"] is False


def test_mcp_exposes_cli_commands_with_safety_defaults():
    specs = build_cli_command_specs()

    assert "cli_status" in specs
    assert "cli_monitor_scan" in specs
    assert specs["cli_status"].read_only is True
    assert specs["cli_monitor_scan"].read_only is False
    assert specs["cli_agent_serve"].long_running is True


def test_mcp_run_cli_blocks_mutation_by_default():
    result = run_generic_cli(config={"agent": {}}, argv=["monitor", "scan"])

    assert result["ok"] is False
    assert "disabled by default" in result["error"]


def make_event(event_type: str, *, course_id: str = "COURSE_A", title: str = "资源") -> CourseUpdateEvent:
    resource_type = event_type.split(".", 1)[0]
    new_hash = f"{event_type}:{course_id}:{title}"
    return CourseUpdateEvent(
        event_id=event_id_for(
            course_id=course_id,
            resource_type=resource_type,
            resource_id=title,
            event_type=event_type,
            new_hash=new_hash,
        ),
        event_type=event_type,
        course_id=course_id,
        course_name="量子力学",
        semester=None,
        resource_type=resource_type,
        resource_id=title,
        resource_title=title,
        source_url=None,
        old_hash=None,
        new_hash=new_hash,
        old_value=None,
        new_value={"title": title},
        changed_fields=["created"],
        detected_at=utc_now(),
        severity="important" if event_type == "assignment.deadline_changed" else "normal",
        summary=f"量子力学 {title}",
        raw=None,
    )
