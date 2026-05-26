from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..monitor.config import database_path, load_config
from ..monitor.models import utc_now
from ..monitor.notifier import BROKER
from ..monitor.store import MonitorStore


def create_app(store: MonitorStore | None = None, config: dict[str, Any] | None = None) -> FastAPI:
    config = config or load_config()
    store = store or MonitorStore(database_path())
    app = FastAPI(title="pkucw agent server")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "pkucw-agent", "ts": utc_now().isoformat()}

    @app.get("/updates")
    def updates(
        course_id: str | None = None,
        event_type: list[str] | None = Query(default=None),
        since: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        events = store.list_events(course_id=course_id, event_types=event_type, since=since, limit=limit)
        return {"updates": [event.to_dict() for event in events]}

    @app.get("/events")
    async def events(
        request: Request,
        course_id: str | None = None,
        event_type: list[str] | None = Query(default=None),
        since: str | None = None,
        token: str | None = None,
        replay_only: bool = False,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        _check_auth(config, request, token)

        async def stream():
            for event in store.list_events(
                course_id=course_id,
                event_types=event_type,
                since=since,
                after_event_id=last_event_id,
                limit=500,
            ):
                yield _sse("course_update", event.event_id, event.to_dict())
            if replay_only:
                return
            q = BROKER.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.to_thread(q.get, True, 30)
                    except Exception:
                        yield _sse("heartbeat", None, {"ts": utc_now().isoformat()})
                        continue
                    if course_id and payload.get("course_id") != course_id:
                        continue
                    if event_type and payload.get("event_type") not in event_type:
                        continue
                    yield _sse("course_update", payload.get("event_id"), payload)
            finally:
                BROKER.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def _check_auth(config: dict[str, Any], request: Request, token: str | None) -> None:
    agent = config.get("agent") or {}
    require = bool(agent.get("require_token", True))
    client_host = request.client.host if request.client else ""
    if not require:
        return
    expected = agent.get("token")
    header = request.headers.get("authorization") or ""
    bearer = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
    if expected and (token == expected or bearer == expected):
        return
    raise HTTPException(status_code=401, detail="unauthorized")


def _sse(event: str, event_id: str | None, data: dict[str, Any]) -> str:
    lines = [f"event: {event}"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def serve(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    config = load_config()
    agent = config.get("agent") or {}
    uvicorn.run(
        create_app(config=config),
        host=host or agent.get("host") or "127.0.0.1",
        port=int(port or agent.get("port") or 8765),
        log_level="info",
    )
