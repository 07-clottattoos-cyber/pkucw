from __future__ import annotations

import json
import sys
from typing import Any

from .cli_bridge import (
    build_cli_command_specs,
    is_cli_tool_name,
    is_command_allowed,
    run_cli_command,
    run_generic_cli,
)
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

    def list_cli_commands(self) -> dict[str, Any]:
        specs = build_cli_command_specs()
        return {
            "commands": [
                {
                    "tool": spec.name,
                    "path": list(spec.path),
                    "usage": spec.usage,
                    "read_only": spec.read_only,
                    "long_running": spec.long_running,
                }
                for spec in specs.values()
            ]
        }

    def run_cli(
        self,
        argv: list[str],
        allow_mutation: bool = False,
        allow_long_running: bool = False,
    ) -> dict[str, Any]:
        return run_generic_cli(
            config=self.config,
            argv=argv,
            allow_mutation=allow_mutation,
            allow_long_running=allow_long_running,
        )


TOOLS = {
    "list_courses": CoursewebMcpTools.list_courses,
    "get_course_snapshot": CoursewebMcpTools.get_course_snapshot,
    "list_recent_updates": CoursewebMcpTools.list_recent_updates,
    "get_update_detail": CoursewebMcpTools.get_update_detail,
    "list_subscriptions": CoursewebMcpTools.list_subscriptions,
    "update_course_subscription": CoursewebMcpTools.update_course_subscription,
    "acknowledge_update": CoursewebMcpTools.acknowledge_update,
    "search_course_resources": CoursewebMcpTools.search_course_resources,
    "list_cli_commands": CoursewebMcpTools.list_cli_commands,
    "run_cli": CoursewebMcpTools.run_cli,
}
RESOURCES = [
    "courseweb://courses",
    "courseweb://courses/{course_id}/snapshot",
    "courseweb://updates/recent",
    "courseweb://subscriptions",
]
PROMPTS = [
    "summarize_today_updates",
    "check_urgent_course_tasks",
    "explain_course_changes",
]


def run_stdio() -> None:
    tools = CoursewebMcpTools()
    cli_specs = build_cli_command_specs()
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
                cli_tools = [spec.to_tool() for spec in cli_specs.values()]
                response["result"] = {
                    "tools": [
                        *_base_tool_descriptors(),
                        *cli_tools,
                    ]
                }
            elif method == "resources/list":
                response["result"] = {
                    "resources": [
                        {"uri": uri, "name": uri.removeprefix("courseweb://"), "mimeType": "application/json"}
                        for uri in RESOURCES
                    ]
                }
            elif method == "resources/read":
                uri = params.get("uri")
                result = _read_resource(tools, uri)
                response["result"] = {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps(result, ensure_ascii=False),
                        }
                    ]
                }
            elif method == "prompts/list":
                response["result"] = {
                    "prompts": [{"name": name, "description": f"pkucw prompt: {name}"} for name in PROMPTS]
                }
            elif method == "prompts/get":
                name = params.get("name")
                response["result"] = {
                    "description": f"pkucw prompt: {name}",
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": _prompt_text(str(name))},
                        }
                    ],
                }
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name in cli_specs:
                    spec = cli_specs[name]
                    args = list(arguments.get("args") or [])
                    ok, error = is_command_allowed(
                        spec,
                        config=tools.config,
                        args=args,
                        allow_mutation=bool(arguments.get("allow_mutation", False)),
                        allow_long_running=bool(arguments.get("allow_long_running", False)),
                    )
                    if not ok:
                        result = {"ok": False, "error": error, "command": name, "path": list(spec.path)}
                    else:
                        result = run_cli_command(
                            spec.path,
                            args=args,
                            force_json=bool(arguments.get("json", True)),
                        )
                elif name in TOOLS:
                    result = TOOLS[name](tools, **arguments)
                elif is_cli_tool_name(str(name)):
                    raise ValueError(f"unknown CLI tool: {name}")
                else:
                    raise ValueError(f"unknown tool: {name}")
                response["result"] = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
            else:
                response["result"] = {}
        except Exception as exc:
            response["error"] = {"code": -32000, "message": str(exc)}
        print(json.dumps(response, ensure_ascii=False), flush=True)


def _base_tool_descriptors() -> list[dict[str, Any]]:
    return [
        {
            "name": "list_courses",
            "description": "List courses from the monitor snapshot store.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_course_snapshot",
            "description": "Get the latest stored CourseSnapshot for a course.",
            "inputSchema": {
                "type": "object",
                "properties": {"course_id": {"type": "string"}},
                "required": ["course_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_recent_updates",
            "description": "List recent stored CourseUpdateEvent records.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "course_id": {"type": ["string", "null"]},
                    "event_types": {"type": ["array", "null"], "items": {"type": "string"}},
                    "since": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "default": 50},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_update_detail",
            "description": "Read one stored CourseUpdateEvent by event_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_subscriptions",
            "description": "List subscription configuration with secrets redacted.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "update_course_subscription",
            "description": "Update one course subscription when agent.allow_modify_subscriptions is enabled.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "enabled": {"type": ["boolean", "null"]},
                    "event_types": {"type": ["array", "null"], "items": {"type": "string"}},
                    "mode": {"type": ["string", "null"]},
                    "channels": {"type": ["array", "null"], "items": {"type": "string"}},
                    "include_sensitive_grade_content": {"type": ["boolean", "null"]},
                },
                "required": ["course_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "acknowledge_update",
            "description": "Mark a stored update as acknowledged.",
            "inputSchema": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_course_resources",
            "description": "Search stored monitor snapshots.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "course_id": {"type": ["string", "null"]},
                    "resource_types": {"type": ["array", "null"], "items": {"type": "string"}},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_cli_commands",
            "description": "List every pkucw CLI command exposed as MCP tools.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "run_cli",
            "description": "Run any pkucw CLI command by argv. Mutating and long-running commands require explicit config opt-in.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}},
                    "allow_mutation": {"type": "boolean", "default": False},
                    "allow_long_running": {"type": "boolean", "default": False},
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
        },
    ]


def _read_resource(tools: CoursewebMcpTools, uri: str | None) -> dict[str, Any]:
    if uri == "courseweb://courses":
        return tools.list_courses()
    if uri == "courseweb://updates/recent":
        return tools.list_recent_updates()
    if uri == "courseweb://subscriptions":
        return tools.list_subscriptions()
    prefix = "courseweb://courses/"
    suffix = "/snapshot"
    if uri and uri.startswith(prefix) and uri.endswith(suffix):
        course_id = uri[len(prefix) : -len(suffix)]
        return tools.get_course_snapshot(course_id)
    raise ValueError(f"unknown resource: {uri}")


def _prompt_text(name: str) -> str:
    if name == "summarize_today_updates":
        return "Summarize today's pkucw course updates. Use list_recent_updates and group by course."
    if name == "check_urgent_course_tasks":
        return "Check urgent or important pkucw updates, especially assignment.deadline_changed and grade.updated."
    if name == "explain_course_changes":
        return "Explain the selected pkucw CourseUpdateEvent in user-facing language with privacy preserved."
    raise ValueError(f"unknown prompt: {name}")
