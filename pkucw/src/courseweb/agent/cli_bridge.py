from __future__ import annotations

import argparse
import contextlib
import io
import re
from dataclasses import dataclass
from typing import Any


TOOL_PREFIX = "cli_"
LONG_RUNNING_PATHS = {
    ("agent", "mcp"),
    ("agent", "serve"),
    ("monitor", "run"),
}
READ_ONLY_PATHS = {
    ("auth", "status"),
    ("status",),
}
MUTATING_PATHS = {
    ("agent", "token"),
    ("monitor", "scan"),
    ("monitor", "test-notify"),
}
MUTATING_TOKENS = {
    "add",
    "auth",
    "clear",
    "delete",
    "dl",
    "download",
    "download-latest",
    "download-assignment",
    "download-content",
    "download-latest-recording",
    "download-recording",
    "latest-recording",
    "login",
    "logout",
    "mute-course",
    "remove",
    "rm",
    "run",
    "serve",
    "submit",
    "submit-assignment",
    "subscribe-course",
    "token",
    "unmute-course",
    "use",
}
SENSITIVE_TOKENS = {
    "--password-stdin",
    "--confirm-final-submit",
    "--final-submit",
}


@dataclass(slots=True)
class CliCommandSpec:
    name: str
    path: tuple[str, ...]
    usage: str
    description: str
    read_only: bool
    long_running: bool

    def to_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                        "description": "Arguments and options after the fixed CLI command path.",
                    },
                    "json": {
                        "type": "boolean",
                        "default": True,
                        "description": "Append --json to the CLI invocation when supported.",
                    },
                    "allow_mutation": {
                        "type": "boolean",
                        "default": False,
                        "description": "Required for commands with side effects, in addition to config opt-in.",
                    },
                    "allow_long_running": {
                        "type": "boolean",
                        "default": False,
                        "description": "Required for long-running commands such as monitor run and agent serve.",
                    },
                },
                "additionalProperties": False,
            },
        }


def build_cli_command_specs() -> dict[str, CliCommandSpec]:
    from ..cli import build_parser

    parser = build_parser()
    specs: dict[str, CliCommandSpec] = {}
    seen_paths: set[tuple[str, ...]] = set()
    _walk_parser(parser, (), specs, seen_paths)
    return specs


def run_cli_command(
    path: tuple[str, ...],
    *,
    args: list[str] | None = None,
    force_json: bool = True,
) -> dict[str, Any]:
    from ..cli import main

    argv = [*path, *(args or [])]
    if force_json and "--json" not in argv:
        argv.append("--json")

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exit_code = main(argv)
        except SystemExit as exc:
            exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        except Exception as exc:
            return {
                "ok": False,
                "exit_code": 1,
                "argv": _scrub_argv(argv),
                "stdout": "",
                "stderr": "",
                "error": _scrub_text(str(exc)),
            }

    output = stdout.getvalue()
    parsed = _parse_json_output(output)
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "argv": _scrub_argv(argv),
        "stdout": _scrub_text(output),
        "stderr": _scrub_text(stderr.getvalue()),
        "json": parsed,
    }


def cli_tool_name(path: tuple[str, ...]) -> str:
    raw = "_".join(path)
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_")
    return f"{TOOL_PREFIX}{normalized}"


def is_cli_tool_name(name: str) -> bool:
    return name.startswith(TOOL_PREFIX)


def is_command_allowed(
    spec: CliCommandSpec,
    *,
    config: dict[str, Any],
    args: list[str],
    allow_mutation: bool,
    allow_long_running: bool,
) -> tuple[bool, str | None]:
    if any(token in SENSITIVE_TOKENS for token in args):
        return False, "sensitive CLI arguments are not allowed through MCP"
    if spec.long_running and not allow_long_running:
        return False, "long-running CLI command requires allow_long_running=true"
    agent_config = config.get("agent") or {}
    if not spec.read_only:
        if not agent_config.get("allow_cli_mutations", False):
            return False, "mutating CLI commands are disabled by default"
        if not allow_mutation:
            return False, "mutating CLI command requires allow_mutation=true"
    if spec.long_running and not agent_config.get("allow_cli_long_running", False):
        return False, "long-running CLI commands are disabled by default"
    return True, None


def run_generic_cli(
    *,
    config: dict[str, Any],
    argv: list[str],
    allow_mutation: bool = False,
    allow_long_running: bool = False,
) -> dict[str, Any]:
    if not argv:
        return {"ok": False, "error": "argv is required"}
    specs = build_cli_command_specs()
    path, rest = _match_command_path(argv, specs)
    if path is None:
        return {"ok": False, "error": f"unknown CLI command path: {' '.join(argv)}"}
    spec = specs[cli_tool_name(path)]
    ok, error = is_command_allowed(
        spec,
        config=config,
        args=rest,
        allow_mutation=allow_mutation,
        allow_long_running=allow_long_running,
    )
    if not ok:
        return {"ok": False, "error": error, "command": spec.name, "path": list(spec.path)}
    return run_cli_command(path, args=rest, force_json=True)


def _walk_parser(
    parser: argparse.ArgumentParser,
    path: tuple[str, ...],
    specs: dict[str, CliCommandSpec],
    seen_paths: set[tuple[str, ...]],
) -> None:
    subparsers = _subparsers(parser)
    if not subparsers:
        if path and path not in seen_paths:
            seen_paths.add(path)
            long_running = path in LONG_RUNNING_PATHS
            read_only = _is_read_only_path(path, long_running)
            name = cli_tool_name(path)
            specs[name] = CliCommandSpec(
                name=name,
                path=path,
                usage=parser.format_usage().strip(),
                description=_description_for_parser(parser, path, read_only, long_running),
                read_only=read_only,
                long_running=long_running,
            )
        return

    for choice, child in subparsers.choices.items():
        if choice.startswith("__"):
            continue
        canonical = getattr(child, "prog", "").split()[len(getattr(parser, "prog", "").split()) :]
        child_path = (*path, choice)
        if canonical and canonical[-1] != choice:
            continue
        _walk_parser(child, child_path, specs, seen_paths)

    if path and "handler" in getattr(parser, "_defaults", {}):
        if path not in seen_paths:
            seen_paths.add(path)
            long_running = path in LONG_RUNNING_PATHS
            read_only = _is_read_only_path(path, long_running)
            name = cli_tool_name(path)
            specs[name] = CliCommandSpec(
                name=name,
                path=path,
                usage=parser.format_usage().strip(),
                description=_description_for_parser(parser, path, read_only, long_running),
                read_only=read_only,
                long_running=long_running,
            )


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _is_read_only_path(path: tuple[str, ...], long_running: bool) -> bool:
    if path in READ_ONLY_PATHS:
        return True
    if long_running or path in MUTATING_PATHS:
        return False
    return not any(token in MUTATING_TOKENS for token in path)


def _description_for_parser(
    parser: argparse.ArgumentParser,
    path: tuple[str, ...],
    read_only: bool,
    long_running: bool,
) -> str:
    help_text = parser.description or parser.format_usage().strip()
    flags = []
    if read_only:
        flags.append("read-only")
    else:
        flags.append("mutating")
    if long_running:
        flags.append("long-running")
    return f"Run `pkucw {' '.join(path)}` via MCP ({', '.join(flags)}). {help_text}"


def _match_command_path(argv: list[str], specs: dict[str, CliCommandSpec]) -> tuple[tuple[str, ...] | None, list[str]]:
    candidates = sorted((spec.path for spec in specs.values()), key=len, reverse=True)
    for path in candidates:
        if tuple(argv[: len(path)]) == path:
            return path, argv[len(path) :]
    return None, argv


def _parse_json_output(output: str) -> Any:
    raw = output.strip()
    if not raw:
        return None
    try:
        return json_loads(raw)
    except Exception:
        return None


def json_loads(raw: str) -> Any:
    import json

    return json.loads(raw)


def _scrub_argv(argv: list[str]) -> list[str]:
    scrubbed: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            scrubbed.append("<redacted>")
            skip_next = False
            continue
        scrubbed.append(token)
        if token.lower() in {"--password", "--token", "--secret", "--webhook-secret"}:
            skip_next = True
    return scrubbed


def _scrub_text(value: str) -> str:
    for marker in ("password", "token", "secret", "cookie"):
        value = re.sub(rf"({marker}\\s*[=:]\\s*)\\S+", r"\1<redacted>", value, flags=re.IGNORECASE)
    return value
