from __future__ import annotations

from typing import Any

from ..monitor.config import ensure_agent_token


def generate_token() -> str:
    return ensure_agent_token()


def public_subscription(subscription: dict[str, Any]) -> dict[str, Any]:
    def scrub(value: Any) -> Any:
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {
                key: "<redacted>"
                if key.lower() in {"token", "secret", "webhook_secret", "authorization"}
                else scrub(item)
                for key, item in value.items()
            }
        return value

    return scrub(subscription)
