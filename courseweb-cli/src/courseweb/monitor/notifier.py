from __future__ import annotations

import hashlib
import hmac
import json
import queue
from dataclasses import dataclass, field
from typing import Any
from urllib.request import Request, urlopen

from .models import DeliveryPlan


@dataclass
class SseBroker:
    max_size: int = 1000
    _subscribers: list[queue.Queue[dict[str, Any]]] = field(default_factory=list)

    def publish(self, event: dict[str, Any]) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                self._subscribers.remove(subscriber)

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_size)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)


BROKER = SseBroker()


class Notifier:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def deliver(self, plan: DeliveryPlan) -> str:
        if plan.channel == "sse":
            BROKER.publish(plan.payload)
            return "delivered"
        if plan.channel == "console":
            print(plan.payload.get("summary", json.dumps(plan.payload, ensure_ascii=False)))
            return "delivered"
        if plan.channel == "webhook":
            self._deliver_webhook(plan)
            return "delivered"
        if plan.channel == "hermes":
            return "pending"
        return "ignored"

    def _deliver_webhook(self, plan: DeliveryPlan) -> None:
        webhook = self.config.get("webhook") or {}
        url = plan.target or webhook.get("url")
        if not url:
            raise RuntimeError("webhook channel requires a URL")
        body = json.dumps(_webhook_payload(plan.payload), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Courseweb-Event": plan.payload["event_type"],
            "X-Courseweb-Event-Id": plan.payload["event_id"],
        }
        secret = webhook.get("secret")
        if secret:
            digest = hmac.new(str(secret).encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-Courseweb-Signature"] = f"sha256={digest}"
        request = Request(url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=10) as response:
            response.read()


def _webhook_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "course_id": event["course_id"],
        "course_name": event["course_name"],
        "summary": event["summary"],
        "severity": event["severity"],
        "detected_at": event["detected_at"],
        "resource": {
            "type": event["resource_type"],
            "id": event.get("resource_id"),
            "title": event.get("resource_title"),
            "url": event.get("source_url"),
        },
        "changed_fields": event.get("changed_fields") or [],
    }
