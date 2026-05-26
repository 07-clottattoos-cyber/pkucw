from __future__ import annotations

import json
import sys
from typing import Any

from ..monitor.config import database_path, load_config, update_course_subscription
from ..monitor.store import MonitorStore
from .schemas import public_subscription


class CoursewebMcpTools:
    def __init__(self, store: MonitorStore | None = None, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        self.store = store or MonitorStore(database_path())

    def list_courses(self) -> dict[str, Any]:
        return {"courses": [snapshot.to_dict() for snapshot in self.store.latest_snapshots()]}

    def get_course_snapshot(self, course_id: str) -> dict[str, Any]:
        snapshot = self.store.latest_snapshot(course_id)
        return {"snapshot": snapshot.to_dict() if snapshot else None}

    def list_recent_updates(
        self,
        course_id: str | None = None,
        event_types: list[str] | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        events = self.store.list_events(course_id=course_id, event_types=event_types, since=since, limit=limit)
        return {"updates": [event.to_dict() for event in events]}

    def get_update_detail(self, event_id: str) -> dict[str, Any]:
        event = self.store.get_event(event_id)
        return {"update": event.to_dict() if event else None}

    def list_subscriptions(self) -> dict[str, Any]:
        return {"subscriptions": [public_subscription(item) for item in self.store.list_subscriptions()]}

    def update_course_subscription(
        self,
        course_id: str,
        enabled: bool | None = None,
        event_types: list[str] | None = None,
        mode: str | None = None,
        channels: list[str] | None = None,
        include_sensitive_grade_content: bool | None = None,
    ) -> dict[str, Any]:
        if not self.config.get("agent", {}).get("allow_modify_subscriptions", False):
            return {"ok": False, "error": "subscription modification is disabled by default"}
        config = update_course_subscription(
            course_id,
            {
                "enabled": enabled,
                "event_types": event_types,
                "mode": mode,
                "channels": channels,
                "include_sensitive_grade_content": include_sensitive_grade_content,
            },
        )
        return {"ok": True, "subscription": public_subscription(config)}

    def acknowledge_update(self, event_id: str) -> dict[str, Any]:
        return {"ok": self.store.acknowledge_event(event_id)}

    def search_course_resources(
        self,
        query: str,
        course_id: str | None = None,
        resource_types: list[str] | None = None,
    ) -> dict[str, Any]:
        needle = query.lower()
        results = []
        snapshots = [self.store.latest_snapshot(course_id)] if course_id else self.store.latest_snapshots()
        for snapshot in [item for item in snapshots if item is not None]:
            for resource_type, items in {
                "announcement": snapshot.announcements,
                "assignment": snapshot.assignments,
                "grade": snapshot.grades,
                "content": snapshot.contents,
                "recording": snapshot.recordings,
            }.items():
                if resource_types and resource_type not in resource_types:
                    continue
                for item in items:
                    text = json.dumps(item.to_dict(), ensure_ascii=False).lower()
                    if needle in text:
                        results.append(
                            {
                                "course_id": snapshot.course_id,
                                "course_name": snapshot.course_name,
                                "resource_type": resource_type,
                                "resource": item.to_dict(),
                            }
                        )
        return {"results": results}


TOOLS = {
    "list_courses": CoursewebMcpTools.list_courses,
    "get_course_snapshot": CoursewebMcpTools.get_course_snapshot,
    "list_recent_updates": CoursewebMcpTools.list_recent_updates,
    "get_update_detail": CoursewebMcpTools.get_update_detail,
    "list_subscriptions": CoursewebMcpTools.list_subscriptions,
    "update_course_subscription": CoursewebMcpTools.update_course_subscription,
    "acknowledge_update": CoursewebMcpTools.acknowledge_update,
    "search_course_resources": CoursewebMcpTools.search_course_resources,
}


def run_stdio() -> None:
    tools = CoursewebMcpTools()
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        response = {"jsonrpc": "2.0", "id": request.get("id")}
        try:
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                response["result"] = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "pkucw", "version": "0.1.0"},
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                }
            elif method == "tools/list":
                response["result"] = {
                    "tools": [{"name": name, "description": f"pkucw {name}"} for name in TOOLS]
                }
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name not in TOOLS:
                    raise ValueError(f"unknown tool: {name}")
                result = TOOLS[name](tools, **arguments)
                response["result"] = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
            else:
                response["result"] = {}
        except Exception as exc:
            response["error"] = {"code": -32000, "message": str(exc)}
        print(json.dumps(response, ensure_ascii=False), flush=True)
