from __future__ import annotations

import json
import os
import sys
from typing import Any


def render_payload(data: dict[str, Any], *, as_json: bool, color: str = "auto") -> str:
    if as_json:
        return json.dumps(data, ensure_ascii=False, indent=2)

    use_color = _should_use_color(color)
    if _looks_like_command_result(data):
        return _render_command_result(data, use_color=use_color)

    lines: list[str] = []
    for key, value in data.items():
        lines.extend(_render_pair(key, value, use_color=use_color))
    return "\n".join(lines)


def _looks_like_command_result(data: dict[str, Any]) -> bool:
    return {"ok", "message", "payload"}.issubset(data.keys())


def _render_command_result(data: dict[str, Any], *, use_color: bool) -> str:
    ok = bool(data.get("ok"))
    message = str(data.get("message") or "").strip()
    payload = data.get("payload")

    sections: list[str] = []
    if message:
        message_text = message if ok else f"错误：{message}"
        sections.append(_style_message(message_text, ok=ok, use_color=use_color))

    if isinstance(payload, dict) and payload:
        lines: list[str] = []
        for key, value in payload.items():
            lines.extend(_render_pair(key, value, use_color=use_color))
        sections.append("\n".join(lines))
    elif payload not in ({}, None):
        sections.append(str(payload))

    if not sections:
        return "成功。" if ok else "错误。"

    return "\n\n".join(section for section in sections if section.strip())


def _render_pair(key: str, value: Any, *, use_color: bool) -> list[str]:
    if isinstance(value, dict):
        lines = [_style_key(f"{key}:", use_color=use_color)]
        for inner_key, inner_value in value.items():
            lines.extend(f"  {line}" for line in _render_pair(inner_key, inner_value, use_color=use_color))
        return lines

    if isinstance(value, list):
        if not value:
            return [f"{_style_key(key, use_color=use_color)}: []"]
        if _should_render_compact_list(key, value):
            return _render_compact_list(key, value, use_color=use_color)
        lines = [_style_key(f"{key}:", use_color=use_color)]
        for item in value:
            if isinstance(item, dict):
                lines.append("  -")
                for inner_key, inner_value in item.items():
                    lines.extend(
                        f"    {line}" for line in _render_pair(inner_key, inner_value, use_color=use_color)
                    )
            else:
                lines.append(f"  - {item}")
        return lines

    return [f"{_style_key(key, use_color=use_color)}: {value}"]


def _should_render_compact_list(key: str, value: list[Any]) -> bool:
    if not value:
        return False
    if not all(isinstance(item, dict) for item in value):
        return False
    return key in {
        "courses",
        "announcements",
        "contents",
        "assignments",
        "recordings",
        "menu_items",
    } or len(value) >= 2


def _render_compact_list(key: str, value: list[Any], *, use_color: bool) -> list[str]:
    lines = [_style_key(f"{key}:", use_color=use_color)]
    for index, item in enumerate(value, start=1):
        lines.append(f"  {_style_index(f'{index}.', use_color=use_color)} {_summarize_mapping(item)}")
    return lines


def _summarize_mapping(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)

    primary = _pick_primary_value(item)
    parts = [primary] if primary else []

    for field in (
        "status",
        "type",
        "term",
        "published_at",
        "recorded_at",
        "due_at",
        "teacher",
        "author",
        "mode",
        "current_page_label",
        "segment_count",
        "duration_seconds",
    ):
        value = item.get(field)
        if value in (None, "", [], {}):
            continue
        if str(value) == primary:
            continue
        parts.append(f"{field}={value}")
        if len(parts) >= 5:
            break

    item_id = item.get("id")
    if item_id not in (None, "", primary):
        parts.append(f"id={item_id}")

    return " | ".join(parts) if parts else json.dumps(item, ensure_ascii=False)


def _pick_primary_value(item: dict[str, Any]) -> str | None:
    for field in ("name", "title", "label", "path", "id"):
        value = item.get(field)
        if value not in (None, "", [], {}):
            return str(value)
    return None


def _should_use_color(mode: str) -> bool:
    normalized = (mode or "auto").strip().lower()
    if normalized == "always":
        return True
    if normalized == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _style_message(text: str, *, ok: bool, use_color: bool) -> str:
    if not use_color:
        return text
    return _ansi(text, "32;1" if ok else "31;1")


def _style_key(text: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return _ansi(text, "36;1")


def _style_index(text: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return _ansi(text, "33;1")


def _ansi(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"
