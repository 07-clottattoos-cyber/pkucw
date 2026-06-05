from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .accounts import (
    AccountError,
    credentials_for_account,
    get_default_account,
    has_saved_password,
    list_accounts,
    prompt_for_credentials,
    remove_account,
    resolve_account,
    set_default_account,
    upsert_account,
)
from .announcements import AnnouncementScrapeError, resolve_announcement, scrape_announcements
from .assignments import (
    AssignmentScrapeError,
    download_assignment,
    resolve_assignment,
    scrape_assignment_detail,
    scrape_assignments,
    submit_assignment,
)
from .auth import AuthError, DEFAULT_LOGIN_URL, login_with_playwright
from .contents import ContentItem, ContentScrapeError, download_content, resolve_content, scrape_contents
from .courses import (
    CourseInfo,
    CourseRecord,
    CourseScrapeError,
    resolve_course,
    resolve_course_matches,
    scrape_course_info,
    scrape_courses,
    suggest_courses,
)
from .grades import GradeScrapeError, resolve_grade, scrape_grades
from .models import CommandResult, SessionState
from .output import render_payload
from .recordings import (
    RecordingScrapeError,
    download_recording,
    resolve_recording,
    scrape_recording_detail,
    scrape_recordings,
)
from .session_runtime import SessionRecoveryError, ensure_live_session
from .state import (
    accounts_path,
    clear_session,
    load_session,
    save_session,
    session_path,
    storage_state_path,
    utc_now_iso,
)


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = "位置参数"
        self._optionals.title = "命令选项"
        if add_help:
            self.add_argument(
                "-h",
                "--help",
                action="help",
                default=argparse.SUPPRESS,
                help="显示帮助信息并退出。",
            )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_agent_argv(argv))

    if not hasattr(args, "handler"):
        parser.print_help()
        return 0

    if getattr(args, "domain", None) == "__complete":
        candidates = _complete_words(build_parser(), getattr(args, "words", []))
        if candidates:
            print("\n".join(candidates))
        return 0

    result = args.handler(args)
    if result is None:
        return 0
    data = result.to_dict()
    print(
        render_payload(
            data,
            as_json=getattr(args, "json", False),
            color=getattr(args, "color", "auto"),
        )
    )
    return 0 if result.ok else 1


_RESOURCE_COMMANDS = {
    "announcements": {"list", "ls", "show", "get"},
    "contents": {"list", "ls", "tree", "show", "get", "download", "dl"},
    "assignments": {"list", "ls", "show", "get", "download", "dl", "submit"},
    "recordings": {"list", "ls", "show", "get", "download", "dl", "download-latest", "latest"},
}
_RESOURCE_ORDERED_DEFAULTS = {"announcements", "contents", "assignments", "recordings"}
_LESSON_ALIASES = {"lesson", "lessons"}
_RESOURCE_ALIASES = {
    "announcements": ["notice", "notices", "announcement", "notification", "notifications"],
    "contents": ["content", "material", "materials"],
    "recordings": ["recording", "video", "videos"],
}
_SINGULAR_RESOURCE_ALIASES = {
    "content": "contents",
    "material": "contents",
    "materials": "contents",
    "recording": "recordings",
    "video": "recordings",
    "videos": "recordings",
}
_RECORDING_TYPE_ALIASES = {"recording", "recordings", "video", "videos"}


def _normalize_agent_argv(argv: list[str] | None) -> list[str]:
    tokens = list(argv if argv is not None else sys.argv[1:])
    if not tokens:
        return tokens

    if tokens[0] in {"notice", "notices", "announcement", "notification", "notifications"}:
        tokens[0] = "announcements"
    elif tokens[0] in _SINGULAR_RESOURCE_ALIASES:
        tokens[0] = _SINGULAR_RESOURCE_ALIASES[tokens[0]]

    if tokens[0] in _LESSON_ALIASES:
        tokens[0] = "contents"

    tokens = _normalize_recording_type_route(tokens)

    if tokens[0] in _RESOURCE_ORDERED_DEFAULTS:
        return _normalize_resource_form(tokens[0], tokens[1:], prefix=[tokens[0]])

    if len(tokens) >= 2 and tokens[0] == "course" and tokens[1] in _RESOURCE_ORDERED_DEFAULTS:
        normalized_tail = _normalize_resource_tail(tokens[1], tokens[2:])
        return ["course", tokens[1], *normalized_tail]

    if len(tokens) >= 3 and tokens[0] == "course" and tokens[2] in _RESOURCE_ORDERED_DEFAULTS:
        course_query = tokens[1]
        normalized_tail = _normalize_resource_tail(tokens[2], tokens[3:])
        if not normalized_tail:
            normalized_tail = ["list"]
        return ["course", tokens[2], normalized_tail[0], course_query, *normalized_tail[1:]]

    return tokens


def _normalize_recording_type_route(tokens: list[str]) -> list[str]:
    if not tokens:
        return tokens
    if tokens[0] != "contents":
        return tokens
    if "--type" not in tokens:
        return tokens
    try:
        type_index = tokens.index("--type")
        type_value = tokens[type_index + 1].strip().lower()
    except (ValueError, IndexError):
        return tokens
    if type_value not in _RECORDING_TYPE_ALIASES:
        return tokens

    rewritten = [token for i, token in enumerate(tokens) if i not in {type_index, type_index + 1}]
    rewritten[0] = "recordings"
    if len(rewritten) == 1:
        rewritten.append("list")
    return rewritten


def _normalize_resource_form(resource: str, tail: list[str], *, prefix: list[str]) -> list[str]:
    normalized_tail = _normalize_resource_tail(resource, tail)
    return [*prefix, *normalized_tail]


def _normalize_resource_tail(resource: str, tail: list[str]) -> list[str]:
    if not tail:
        return ["list"]

    known_commands = _RESOURCE_COMMANDS[resource]
    first = tail[0]
    second = tail[1] if len(tail) > 1 else None

    if first in {"-h", "--help"}:
        return tail

    if first in _LESSON_ALIASES:
        first = "contents"

    if first in known_commands:
        return [first, *tail[1:]]

    if first.startswith("-"):
        return ["list", *tail]

    if second in known_commands:
        return [second, first, *tail[2:]]

    return ["list", *tail]


def build_parser() -> argparse.ArgumentParser:
    shared_parser = ChineseArgumentParser(add_help=False)
    shared_parser._optionals.title = "通用输出选项"
    shared_parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果。",
    )
    shared_parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="控制人类可读输出的颜色。",
    )

    parser = ChineseArgumentParser(
        prog="pkucw",
        description="面向北大教学网的命令行工具，使用真实浏览器会话驱动各类操作。",
        epilog=(
            "常见用法：\n"
            "  pkucw login\n"
            "  pkucw ls --current\n"
            "  pkucw use \"有机化学 (一)\"\n"
            "  pkucw announcements list\n"
            "  pkucw recordings latest --output ./downloads/latest\n"
            "\n兼容别名：cw, courseweb\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[shared_parser],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="显示版本号并退出。",
    )

    subparsers = parser.add_subparsers(
        dest="domain",
        metavar="{completion,auth,accounts,login,logout,status,courses,ls,use,current,doctor,course,info,announcements,contents,assignments,recordings,grades,monitor,agent}",
    )

    add_completion_parsers(subparsers, shared_parser)
    add_auth_parsers(subparsers, shared_parser)
    add_accounts_parsers(subparsers, shared_parser)
    add_auth_shortcuts(subparsers, shared_parser)
    add_courses_parsers(subparsers, shared_parser)
    add_context_parsers(subparsers, shared_parser)
    add_course_parsers(subparsers, shared_parser)
    add_top_level_course_resource_parsers(subparsers, shared_parser)
    add_grade_parsers(subparsers, shared_parser)
    add_monitor_parsers(subparsers, shared_parser)
    add_agent_parsers(subparsers, shared_parser)
    add_agent_compat_parsers(subparsers, shared_parser)

    return parser


def add_completion_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    completion_parser = subparsers.add_parser(
        "completion",
        help="输出 shell 补全脚本。",
        parents=[shared_parser],
    )
    completion_parser.add_argument("shell", choices=["bash", "zsh", "fish"], help="终端 shell 类型。")
    completion_parser.set_defaults(handler=handle_completion_script)

    complete_parser = subparsers.add_parser(
        "__complete",
        help=argparse.SUPPRESS,
        parents=[shared_parser],
    )
    complete_parser.add_argument("words", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    complete_parser.set_defaults(handler=handle_completion_candidates)
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if action.dest != "__complete"
    ]


def add_auth_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    auth_parser = subparsers.add_parser(
        "auth",
        help="管理登录会话。",
        parents=[shared_parser],
    )
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command")

    login_parser = auth_subparsers.add_parser(
        "login",
        help="执行真实浏览器登录并保存会话状态。",
        parents=[shared_parser],
    )
    _add_login_arguments(login_parser)
    login_parser.set_defaults(handler=handle_auth_login)

    logout_parser = auth_subparsers.add_parser(
        "logout",
        help="清除本地会话状态。",
        parents=[shared_parser],
    )
    logout_parser.set_defaults(handler=handle_auth_logout)

    status_parser = auth_subparsers.add_parser(
        "status",
        help="查看本地会话状态。",
        parents=[shared_parser],
    )
    status_parser.set_defaults(handler=handle_auth_status)


def add_accounts_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    accounts_parser = subparsers.add_parser(
        "accounts",
        help="管理保存在 macOS 钥匙串中的账号。",
        aliases=["account"],
        parents=[shared_parser],
    )
    accounts_subparsers = accounts_parser.add_subparsers(dest="accounts_command")

    list_parser = accounts_subparsers.add_parser(
        "list",
        help="列出已保存账号。",
        aliases=["ls"],
        parents=[shared_parser],
    )
    list_parser.set_defaults(handler=handle_accounts_list)

    show_parser = accounts_subparsers.add_parser(
        "show",
        help="查看单个已保存账号。",
        aliases=["get"],
        parents=[shared_parser],
    )
    show_parser.add_argument("account", nargs="?", help="已保存账号的用户名或标签。")
    show_parser.set_defaults(handler=handle_accounts_show)

    add_parser = accounts_subparsers.add_parser(
        "add",
        help="添加或更新已保存账号。",
        parents=[shared_parser],
    )
    add_parser.add_argument("--username", help="要保存的北大账号。")
    add_parser.add_argument("--label", help="账号的可选备注标签。")
    add_parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取密码，而不是终端交互输入。",
    )
    add_parser.add_argument(
        "--default",
        action="store_true",
        help="将该账号设为默认账号，供后续 `pkucw login` 使用。",
    )
    add_parser.set_defaults(handler=handle_accounts_add)

    use_parser = accounts_subparsers.add_parser(
        "use",
        help="设置默认账号。",
        parents=[shared_parser],
    )
    use_parser.add_argument("account", help="已保存账号的用户名或标签。")
    use_parser.set_defaults(handler=handle_accounts_use)

    remove_parser = accounts_subparsers.add_parser(
        "remove",
        help="删除已保存账号，并从 macOS 钥匙串中移除对应密码。",
        aliases=["rm", "delete"],
        parents=[shared_parser],
    )
    remove_parser.add_argument("account", help="已保存账号的用户名或标签。")
    remove_parser.set_defaults(handler=handle_accounts_remove)


def _add_login_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account", help="已保存账号的用户名或标签。")
    parser.add_argument("--username", help="本次登录使用的北大账号。")
    parser.add_argument("--label", help="保存账号时使用的可选标签。")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取密码，而不是在终端里输入。",
    )
    parser.add_argument(
        "--no-save-account",
        action="store_true",
        help="登录成功后，不保存或更新账号信息。",
    )
    parser.add_argument(
        "--login-url",
        default=DEFAULT_LOGIN_URL,
        help="教学网登录入口地址。",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="登录时显示 Chromium 浏览器窗口，而不是无头模式。",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="每个登录步骤的浏览器超时时间（秒）。",
    )


def add_auth_shortcuts(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    login_parser = subparsers.add_parser(
        "login",
        help="`auth login` 的快捷入口。",
        parents=[shared_parser],
    )
    _add_login_arguments(login_parser)
    login_parser.set_defaults(handler=handle_auth_login)

    logout_parser = subparsers.add_parser(
        "logout",
        help="`auth logout` 的快捷入口。",
        parents=[shared_parser],
    )
    logout_parser.set_defaults(handler=handle_auth_logout)

    status_parser = subparsers.add_parser(
        "status",
        help="`auth status` 的快捷入口。",
        parents=[shared_parser],
    )
    status_parser.set_defaults(handler=handle_auth_status)


def _add_optional_course_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--course",
        dest="course_option",
        help="通过旗标显式指定课程；可与位置参数二选一。",
    )


def add_courses_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    courses_parser = subparsers.add_parser(
        "courses",
        help="查看课程列表。",
        parents=[shared_parser],
    )
    courses_subparsers = courses_parser.add_subparsers(dest="courses_command")
    courses_parser.set_defaults(handler=handle_courses_list)

    list_parser = courses_subparsers.add_parser(
        "list",
        help="列出课程。",
        aliases=["ls"],
        parents=[shared_parser],
    )
    list_parser.add_argument("--current", action="store_true", help="只显示当前学期课程。")
    list_parser.add_argument("--archived", action="store_true", help="只显示历史课程。")
    list_parser.add_argument("--search", help="按课程标题、ID 或学期筛选课程。")
    list_parser.add_argument("query", nargs="?", help=argparse.SUPPRESS)
    list_parser.set_defaults(handler=handle_courses_list)

    show_parser = courses_subparsers.add_parser(
        "show",
        help="查看单门课程的匹配结果。",
        aliases=["get"],
        parents=[shared_parser],
    )
    show_parser.add_argument("course", help="课程 ID、短标识或标题片段。")
    show_parser.set_defaults(handler=handle_courses_show)

    current_parser = courses_subparsers.add_parser(
        "current",
        help="查看当前会话里保存的活动课程。",
        parents=[shared_parser],
    )
    current_parser.set_defaults(handler=handle_current_course)


def add_context_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    ls_parser = subparsers.add_parser(
        "ls",
        help="`courses list` 的快捷入口。",
        parents=[shared_parser],
    )
    ls_parser.add_argument("--current", action="store_true", help="只显示当前学期课程。")
    ls_parser.add_argument("--archived", action="store_true", help="只显示历史课程。")
    ls_parser.add_argument("--search", help="按课程标题、ID 或学期筛选课程。")
    ls_parser.add_argument("query", nargs="?", help=argparse.SUPPRESS)
    ls_parser.set_defaults(handler=handle_courses_list)

    use_parser = subparsers.add_parser(
        "use",
        help="设置活动课程，后续命令可省略课程参数。",
        parents=[shared_parser],
    )
    use_parser.add_argument("course", help="课程 ID、短标识或标题片段。")
    use_parser.set_defaults(handler=handle_use_course)

    current_parser = subparsers.add_parser(
        "current",
        help="查看当前活动课程上下文。",
        parents=[shared_parser],
    )
    current_parser.set_defaults(handler=handle_current_course)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="查看安装、会话和上下文诊断信息。",
        parents=[shared_parser],
    )
    doctor_parser.set_defaults(handler=handle_doctor)


def add_course_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    course_parser = subparsers.add_parser(
        "course",
        help="在单门课程内操作。",
        parents=[shared_parser],
    )
    course_subparsers = course_parser.add_subparsers(dest="course_command")

    course_list_parser = course_subparsers.add_parser(
        "list",
        help=argparse.SUPPRESS,
        aliases=["ls"],
        parents=[shared_parser],
    )
    course_list_parser.add_argument("--current", action="store_true", help=argparse.SUPPRESS)
    course_list_parser.add_argument("--archived", action="store_true", help=argparse.SUPPRESS)
    course_list_parser.add_argument("--search", help=argparse.SUPPRESS)
    course_list_parser.add_argument("query", nargs="?", help=argparse.SUPPRESS)
    course_list_parser.set_defaults(handler=handle_courses_list)

    info_parser = course_subparsers.add_parser(
        "info",
        help="查看课程元数据。",
        parents=[shared_parser],
    )
    info_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    info_parser.set_defaults(handler=handle_course_info)

    add_named_resource_parsers(
        course_subparsers,
        shared_parser,
        "announcements",
        supports_submit=False,
    )
    add_named_resource_parsers(course_subparsers, shared_parser, "contents", supports_submit=False)
    add_named_resource_parsers(
        course_subparsers,
        shared_parser,
        "assignments",
        supports_submit=True,
    )
    add_recording_parsers(course_subparsers, shared_parser)


def add_top_level_course_resource_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    info_parser = subparsers.add_parser(
        "info",
        help="`course info` 的快捷入口；省略时使用当前活动课程。",
        parents=[shared_parser],
    )
    info_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    info_parser.set_defaults(handler=handle_course_info)

    add_named_resource_parsers(subparsers, shared_parser, "announcements", supports_submit=False)
    add_named_resource_parsers(subparsers, shared_parser, "contents", supports_submit=False)
    add_named_resource_parsers(subparsers, shared_parser, "assignments", supports_submit=True)
    add_recording_parsers(subparsers, shared_parser)


def add_grade_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    grades_parser = subparsers.add_parser(
        "grades",
        help="查看课程成绩。",
        aliases=["grade"],
        parents=[shared_parser],
    )
    _add_optional_course_argument(grades_parser)
    grades_subparsers = grades_parser.add_subparsers(dest="grades_command")
    grades_parser.set_defaults(handler=handle_course_grades_list, course=None)

    list_parser = grades_subparsers.add_parser(
        "list",
        help="列出课程成绩。",
        aliases=["ls"],
        parents=[shared_parser],
    )
    list_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(list_parser)
    list_parser.set_defaults(handler=handle_course_grades_list)

    show_parser = grades_subparsers.add_parser(
        "show",
        help="查看单项成绩。",
        aliases=["get"],
        parents=[shared_parser],
    )
    show_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(show_parser)
    show_parser.add_argument("grade", help="成绩项目 ID 或标题片段。")
    show_parser.set_defaults(handler=handle_course_grades_show)


def add_monitor_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    monitor_parser = subparsers.add_parser("monitor", help="长期监控课程网资源。", parents=[shared_parser])
    monitor_subparsers = monitor_parser.add_subparsers(dest="monitor_command")

    scan_parser = monitor_subparsers.add_parser("scan", help="执行一次扫描并写入 baseline/events。", parents=[shared_parser])
    scan_parser.set_defaults(handler=handle_monitor_scan)

    run_parser = monitor_subparsers.add_parser("run", help="启动长期轮询监控。", parents=[shared_parser])
    run_parser.set_defaults(handler=handle_monitor_run)

    status_parser = monitor_subparsers.add_parser("status", help="查看监控状态。", parents=[shared_parser])
    status_parser.set_defaults(handler=handle_monitor_status)

    updates_parser = monitor_subparsers.add_parser("updates", help="列出最近更新。", parents=[shared_parser])
    updates_parser.add_argument("--since", help="ISO 时间下限。")
    updates_parser.add_argument("--course-id", help="课程 ID。")
    updates_parser.add_argument("--event-type", action="append", help="事件类型，可重复。")
    updates_parser.add_argument("--limit", type=int, default=50, help="返回数量。")
    updates_parser.set_defaults(handler=handle_monitor_updates)

    subscribe_parser = monitor_subparsers.add_parser("subscribe-course", help="配置课程级订阅。", parents=[shared_parser])
    subscribe_parser.add_argument("course_id", help="课程 ID。")
    subscribe_parser.add_argument("--mode", choices=["realtime", "digest", "manual", "hybrid"], help="订阅模式。")
    subscribe_parser.add_argument("--channel", action="append", dest="channels", help="通知通道，可重复。")
    subscribe_parser.add_argument("--event-type", action="append", dest="event_types", help="事件类型，可重复。")
    subscribe_parser.add_argument("--include-sensitive-grade-content", action="store_true", help="允许该课程推送具体成绩内容。")
    subscribe_parser.set_defaults(handler=handle_monitor_subscribe_course)

    mute_parser = monitor_subparsers.add_parser("mute-course", help="静音某门课。", parents=[shared_parser])
    mute_parser.add_argument("course_id", help="课程 ID。")
    mute_parser.set_defaults(handler=handle_monitor_mute_course)

    unmute_parser = monitor_subparsers.add_parser("unmute-course", help="取消某门课静音。", parents=[shared_parser])
    unmute_parser.add_argument("course_id", help="课程 ID。")
    unmute_parser.set_defaults(handler=handle_monitor_unmute_course)

    test_parser = monitor_subparsers.add_parser("test-notify", help="发送一条本地测试通知。", parents=[shared_parser])
    test_parser.set_defaults(handler=handle_monitor_test_notify)


def add_agent_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    agent_parser = subparsers.add_parser("agent", help="Agent 信息源与实时事件服务。", parents=[shared_parser])
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command")

    serve_parser = agent_subparsers.add_parser("serve", help="启动 HTTP/SSE agent server。", parents=[shared_parser])
    serve_parser.add_argument("--host", default=None, help="监听地址，默认 127.0.0.1。")
    serve_parser.add_argument("--port", type=int, default=None, help="监听端口。")
    serve_parser.set_defaults(handler=handle_agent_serve)

    mcp_parser = agent_subparsers.add_parser("mcp", help="启动 MCP stdio server。", parents=[shared_parser])
    mcp_parser.set_defaults(handler=handle_agent_mcp)

    token_parser = agent_subparsers.add_parser("token", help="生成或显示 agent server bearer token。", parents=[shared_parser])
    token_parser.set_defaults(handler=handle_agent_token)


def add_named_resource_parsers(
    course_subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
    name: str,
    *,
    supports_submit: bool,
) -> None:
    resource_label = _resource_label(name)
    singular_label = _resource_singular_label(name)
    parser = course_subparsers.add_parser(
        name,
        help=f"管理{resource_label}。",
        aliases=_RESOURCE_ALIASES.get(name, []),
        parents=[shared_parser],
    )
    _add_optional_course_argument(parser)
    if name == "announcements":
        parser.add_argument(
            "--limit",
            type=int,
            help="只返回前 N 条通知；未提供时返回全部通知。",
        )
    subparsers = parser.add_subparsers(dest=f"{name}_command")

    list_parser = subparsers.add_parser(
        "list",
        help=f"列出{resource_label}。",
        aliases=["ls"],
        parents=[shared_parser],
    )
    list_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(list_parser)
    if name == "announcements":
        list_parser.add_argument(
            "--limit",
            type=int,
            help="只返回前 N 条通知；未提供时返回全部通知。",
        )
    if name == "assignments":
        list_parser.set_defaults(handler=handle_course_assignments_list)
        parser.set_defaults(handler=handle_course_assignments_list, course=None)
    elif name == "announcements":
        list_parser.set_defaults(handler=handle_course_announcements_list)
        parser.set_defaults(handler=handle_course_announcements_list, course=None)
    elif name == "contents":
        list_parser.set_defaults(handler=handle_course_contents_list)
        parser.set_defaults(handler=handle_course_contents_list, course=None)
    else:
        list_parser.set_defaults(
            handler=make_placeholder_handler(
                f"course {name} list",
                _resource_plan_steps(name, "list"),
            )
        )

    if name == "contents":
        tree_parser = subparsers.add_parser(
            "tree",
            help="以树形方式显示教学内容。",
            parents=[shared_parser],
        )
        tree_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
        _add_optional_course_argument(tree_parser)
        tree_parser.set_defaults(handler=handle_course_contents_tree)

        show_parser = subparsers.add_parser(
            "show",
            help="查看单个教学内容。",
            aliases=["get"],
            parents=[shared_parser],
        )
        show_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
        _add_optional_course_argument(show_parser)
        show_parser.add_argument("content", help="教学内容 ID 或标题片段。")
        show_parser.set_defaults(handler=handle_course_contents_show)

        download_parser = subparsers.add_parser(
            "download",
            help="下载教学内容。",
            aliases=["dl"],
            parents=[shared_parser],
        )
        download_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
        _add_optional_course_argument(download_parser)
        download_parser.add_argument("content", help="教学内容 ID 或标题片段。")
        download_parser.add_argument(
            "--output",
            type=Path,
            help="下载文件或文件夹的可选输出路径。",
        )
        download_parser.add_argument(
            "--output-dir",
            type=Path,
            help="仅指定输出目录；会自动使用教学内容标题生成文件名。",
        )
        download_parser.add_argument(
            "--dest",
            type=Path,
            help=argparse.SUPPRESS,
        )
        download_parser.set_defaults(handler=handle_course_contents_download)
        return

    show_parser = subparsers.add_parser(
        "show",
        help=f"查看单条{singular_label}详情。",
        aliases=["get"],
        parents=[shared_parser],
    )
    show_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(show_parser)
    show_parser.add_argument(name[:-1], help=f"{singular_label} ID 或标题片段。")
    if name == "assignments":
        show_parser.set_defaults(handler=handle_course_assignments_show)
    elif name == "announcements":
        show_parser.set_defaults(handler=handle_course_announcements_show)
    else:
        show_parser.set_defaults(
            handler=make_placeholder_handler(
                f"course {name} show",
                _resource_plan_steps(name, "show"),
            )
        )

    if supports_submit:
        download_parser = subparsers.add_parser(
            "download",
            help="下载作业说明和附件。",
            aliases=["dl"],
            parents=[shared_parser],
        )
        download_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
        _add_optional_course_argument(download_parser)
        download_parser.add_argument("assignment", help="作业 ID 或标题片段。")
        download_parser.add_argument(
            "--output",
            type=Path,
            help="下载目录或输出前缀；默认使用作业标题创建目录。",
        )
        download_parser.add_argument(
            "--output-dir",
            type=Path,
            help="仅指定输出目录；会自动使用作业标题创建目录。",
        )
        download_parser.add_argument(
            "--dest",
            type=Path,
            help=argparse.SUPPRESS,
        )
        download_parser.set_defaults(handler=handle_course_assignments_download)

        submit_parser = subparsers.add_parser(
            "submit",
            help="提交 Blackboard 站内作业。",
            parents=[shared_parser],
        )
        submit_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
        _add_optional_course_argument(submit_parser)
        submit_parser.add_argument("assignment", help="作业 ID 或标题片段。")
        submit_parser.add_argument("--file", type=Path, action="append", default=[], help="要上传的文件。")
        submit_parser.add_argument(
            "--replace-files",
            action="store_true",
            help="上传新文件前，先移除现有草稿附件。",
        )
        submit_parser.add_argument(
            "--clear-files",
            action="store_true",
            help="移除现有草稿附件，但不新增文件。",
        )
        submit_parser.add_argument("--text", help="文本提交内容。")
        submit_parser.add_argument(
            "--clear-text",
            action="store_true",
            help="清空当前草稿中的文本提交内容。",
        )
        submit_parser.add_argument("--comment", help="可选提交备注。")
        submit_parser.add_argument(
            "--clear-comment",
            action="store_true",
            help="清空当前草稿备注。",
        )
        submit_parser.add_argument(
            "--final-submit",
            action="store_true",
            help="执行真实最终提交，而不是仅保存草稿。",
        )
        submit_parser.add_argument(
            "--confirm-final-submit",
            help="`--final-submit` 的二次确认，必须与作业 ID 或标题完全一致。",
        )
        submit_parser.add_argument(
            "--save-draft",
            action="store_true",
            help="执行真实草稿保存。",
        )
        submit_parser.set_defaults(handler=handle_assignment_submit)


def add_recording_parsers(
    course_subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    parser = course_subparsers.add_parser(
        "recordings",
        help="管理课堂实录。",
        parents=[shared_parser],
    )
    _add_optional_course_argument(parser)
    subparsers = parser.add_subparsers(dest="recordings_command")
    parser.set_defaults(handler=handle_course_recordings_list, course=None)

    list_parser = subparsers.add_parser(
        "list",
        help="列出课堂实录。",
        aliases=["ls"],
        parents=[shared_parser],
    )
    list_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(list_parser)
    list_parser.set_defaults(handler=handle_course_recordings_list)

    show_parser = subparsers.add_parser(
        "show",
        help="查看单条课堂实录详情。",
        aliases=["get"],
        parents=[shared_parser],
    )
    show_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(show_parser)
    show_parser.add_argument("recording", help="课堂实录 ID 或标题片段。")
    show_parser.set_defaults(handler=handle_course_recordings_show)

    download_parser = subparsers.add_parser(
        "download",
        help="下载单条课堂实录。",
        aliases=["dl"],
        parents=[shared_parser],
    )
    download_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(download_parser)
    download_parser.add_argument("recording", help="课堂实录 ID 或标题片段。")
    download_parser.add_argument("--output", type=Path, help="可选输出路径。")
    download_parser.add_argument(
        "--output-dir",
        type=Path,
        help="仅指定输出目录；会自动使用课堂实录标题生成文件名。",
    )
    download_parser.add_argument(
        "--dest",
        type=Path,
        help=argparse.SUPPRESS,
    )
    download_parser.add_argument(
        "--no-remux",
        action="store_true",
        help="保留解密后的 .ts 文件，并跳过 mp4 转封装。",
    )
    download_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="不在 stderr 中显示分片下载进度。",
    )
    download_parser.set_defaults(handler=handle_course_recordings_download)

    latest_parser = subparsers.add_parser(
        "download-latest",
        help="下载最新一条课堂实录。",
        aliases=["latest"],
        parents=[shared_parser],
    )
    latest_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(latest_parser)
    latest_parser.add_argument("--output", type=Path, help="可选输出路径。")
    latest_parser.add_argument(
        "--output-dir",
        type=Path,
        help="仅指定输出目录；会自动使用课堂实录标题生成文件名。",
    )
    latest_parser.add_argument(
        "--dest",
        type=Path,
        help=argparse.SUPPRESS,
    )
    latest_parser.add_argument(
        "--no-remux",
        action="store_true",
        help="保留解密后的 .ts 文件，并跳过 mp4 转封装。",
    )
    latest_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="不在 stderr 中显示分片下载进度。",
    )
    latest_parser.set_defaults(handler=handle_course_recordings_download_latest)


def add_agent_compat_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    shared_parser: argparse.ArgumentParser,
) -> None:
    content_parser = subparsers.add_parser(
        "download-content",
        help=argparse.SUPPRESS,
        parents=[shared_parser],
    )
    content_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(content_parser)
    content_parser.add_argument("content", help="教学内容 ID 或标题片段。")
    content_parser.add_argument("--output", type=Path, help="下载文件或文件夹的可选输出路径。")
    content_parser.add_argument("--output-dir", type=Path, help="仅指定输出目录；会自动使用教学内容标题生成文件名。")
    content_parser.add_argument("--dest", type=Path, help="`--output-dir` 的兼容别名。")
    content_parser.set_defaults(handler=handle_download_content_shortcut)

    recording_parser = subparsers.add_parser(
        "download-recording",
        help=argparse.SUPPRESS,
        parents=[shared_parser],
    )
    recording_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(recording_parser)
    recording_parser.add_argument("recording", nargs="?", help="课堂实录 ID 或标题片段。")
    recording_parser.add_argument("--latest", action="store_true", help="下载最新一条课堂实录。")
    recording_parser.add_argument("--output", type=Path, help="可选输出路径。")
    recording_parser.add_argument("--output-dir", type=Path, help="仅指定输出目录；会自动使用课堂实录标题生成文件名。")
    recording_parser.add_argument("--dest", type=Path, help="`--output-dir` 的兼容别名。")
    recording_parser.add_argument("--no-remux", action="store_true", help="保留 .ts 文件并跳过 mp4 转封装。")
    recording_parser.add_argument("--no-progress", action="store_true", help="不显示分片下载进度。")
    recording_parser.set_defaults(handler=handle_download_recording_shortcut)

    latest_recording_parser = subparsers.add_parser(
        "download-latest-recording",
        help=argparse.SUPPRESS,
        aliases=["latest-recording"],
        parents=[shared_parser],
    )
    latest_recording_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(latest_recording_parser)
    latest_recording_parser.add_argument("--output", type=Path, help="可选输出路径。")
    latest_recording_parser.add_argument("--output-dir", type=Path, help="仅指定输出目录；会自动使用课堂实录标题生成文件名。")
    latest_recording_parser.add_argument("--dest", type=Path, help="`--output-dir` 的兼容别名。")
    latest_recording_parser.add_argument(
        "--no-remux",
        action="store_true",
        help="保留 .ts 文件并跳过 mp4 转封装。",
    )
    latest_recording_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="不显示分片下载进度。",
    )
    latest_recording_parser.set_defaults(
        handler=handle_course_recordings_download_latest,
        no_remux=True,
        no_progress=True,
    )

    assignment_parser = subparsers.add_parser(
        "download-assignment",
        help=argparse.SUPPRESS,
        parents=[shared_parser],
    )
    assignment_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(assignment_parser)
    assignment_parser.add_argument("assignment", help="作业 ID 或标题片段。")
    assignment_parser.add_argument("--output", type=Path, help="下载目录或输出前缀；默认使用作业标题创建目录。")
    assignment_parser.add_argument("--output-dir", type=Path, help="仅指定输出目录；会自动使用作业标题创建目录。")
    assignment_parser.add_argument("--dest", type=Path, help="`--output-dir` 的兼容别名。")
    assignment_parser.set_defaults(handler=handle_course_assignments_download)

    submit_parser = subparsers.add_parser(
        "submit-assignment",
        help=argparse.SUPPRESS,
        parents=[shared_parser],
    )
    submit_parser.add_argument("course", nargs="?", help="课程 ID、短标识或标题片段。")
    _add_optional_course_argument(submit_parser)
    submit_parser.add_argument("assignment", help="作业 ID 或标题片段。")
    submit_parser.add_argument("--file", type=Path, action="append", default=[], help="要上传的文件。")
    submit_parser.add_argument("--replace-files", action="store_true", help="上传新文件前，先移除现有草稿附件。")
    submit_parser.add_argument("--clear-files", action="store_true", help="移除现有草稿附件，但不新增文件。")
    submit_parser.add_argument("--text", help="文本提交内容。")
    submit_parser.add_argument("--clear-text", action="store_true", help="清空当前草稿中的文本提交内容。")
    submit_parser.add_argument("--comment", help="可选提交备注。")
    submit_parser.add_argument("--clear-comment", action="store_true", help="清空当前草稿备注。")
    submit_parser.add_argument("--final-submit", action="store_true", help="执行真实最终提交，而不是仅保存草稿。")
    submit_parser.add_argument("--confirm-final-submit", help="`--final-submit` 的二次确认，必须与作业 ID 或标题完全一致。")
    submit_parser.add_argument("--save-draft", action="store_true", help="执行真实草稿保存。")
    submit_parser.set_defaults(handler=handle_assignment_submit)

    list_courses_parser = subparsers.add_parser(
        "list-courses",
        help=argparse.SUPPRESS,
        parents=[shared_parser],
    )
    list_courses_parser.add_argument("--current", action="store_true", help=argparse.SUPPRESS)
    list_courses_parser.add_argument("--archived", action="store_true", help=argparse.SUPPRESS)
    list_courses_parser.add_argument("--search", help=argparse.SUPPRESS)
    list_courses_parser.add_argument("query", nargs="?", help=argparse.SUPPRESS)
    list_courses_parser.set_defaults(handler=handle_courses_list)


def _normalize_output_path(args: argparse.Namespace) -> str | None:
    output = getattr(args, "output", None)
    if output:
        return str(output.expanduser().resolve())

    dest = getattr(args, "dest", None)
    if dest:
        resolved = dest.expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return str(resolved)

    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        resolved = output_dir.expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return str(resolved)

    return None


def _course_query(args: argparse.Namespace) -> str | None:
    return getattr(args, "course_option", None) or getattr(args, "course", None)


def handle_download_content_shortcut(args: argparse.Namespace) -> CommandResult:
    args.domain = "contents"
    return handle_course_contents_download(args)


def handle_download_recording_shortcut(args: argparse.Namespace) -> CommandResult:
    if getattr(args, "latest", False) or not getattr(args, "recording", None):
        return handle_course_recordings_download_latest(args)
    return handle_course_recordings_download(args)


def handle_course_grades_list(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error
    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error
    assert course is not None
    try:
        info, grades = scrape_grades(storage_state_path=session.storage_state or "", course=course, headless=True)
    except GradeScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})
    return CommandResult(
        ok=True,
        message=f"找到 {len(grades)} 条成绩。",
        payload={
            "course": course.to_dict(),
            "page": {
                "title": info.page_title,
                "url": info.current_page_url,
                "label": info.current_page_label,
            },
            "grades": [grade.to_dict() for grade in grades],
        },
    )


def handle_course_grades_show(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error
    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error
    assert course is not None
    try:
        _, grades = scrape_grades(storage_state_path=session.storage_state or "", course=course, headless=True)
    except GradeScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})
    grade = resolve_grade(grades, args.grade)
    if grade is None:
        return CommandResult(ok=False, message="没有找到匹配的成绩项目。", payload={"course": course.to_dict()})
    return CommandResult(ok=True, message=grade.title, payload={"course": course.to_dict(), "grade": grade.to_dict()})


def handle_monitor_scan(args: argparse.Namespace) -> CommandResult:
    from .monitor.service import MonitorService

    try:
        result = MonitorService().scan(notify=True)
    except Exception as exc:
        return CommandResult(ok=False, message=_scrub_sensitive_message(str(exc)), payload={})
    return CommandResult(ok=True, message=f"扫描完成，生成 {result['events']} 个事件。", payload=result)


def handle_monitor_run(args: argparse.Namespace) -> CommandResult:
    from .monitor.service import MonitorService

    try:
        MonitorService().run_forever()
    except Exception as exc:
        return CommandResult(ok=False, message=_scrub_sensitive_message(str(exc)), payload={})
    return CommandResult(ok=True, message="monitor stopped", payload={})


def handle_monitor_status(args: argparse.Namespace) -> CommandResult:
    from .monitor.config import database_path
    from .monitor.store import MonitorStore

    store = MonitorStore(database_path())
    return CommandResult(ok=True, message="监控状态。", payload={"status": store.status()})


def handle_monitor_updates(args: argparse.Namespace) -> CommandResult:
    from .monitor.config import database_path
    from .monitor.store import MonitorStore

    store = MonitorStore(database_path())
    events = store.list_events(
        course_id=args.course_id,
        event_types=args.event_type,
        since=args.since,
        limit=args.limit,
    )
    return CommandResult(ok=True, message=f"找到 {len(events)} 条更新。", payload={"updates": [event.to_dict() for event in events]})


def handle_monitor_subscribe_course(args: argparse.Namespace) -> CommandResult:
    from .monitor.config import update_course_subscription

    patch = {
        "enabled": True,
        "mode": args.mode,
        "channels": args.channels,
        "event_types": args.event_types,
        "include_sensitive_grade_content": True if args.include_sensitive_grade_content else None,
    }
    subscription = update_course_subscription(args.course_id, patch)
    return CommandResult(ok=True, message=f"已更新课程订阅：{args.course_id}", payload={"subscription": subscription})


def handle_monitor_mute_course(args: argparse.Namespace) -> CommandResult:
    from .monitor.config import update_course_subscription

    subscription = update_course_subscription(args.course_id, {"enabled": False, "channels": [], "event_types": []})
    return CommandResult(ok=True, message=f"已静音课程：{args.course_id}", payload={"subscription": subscription})


def handle_monitor_unmute_course(args: argparse.Namespace) -> CommandResult:
    from .monitor.config import update_course_subscription

    subscription = update_course_subscription(args.course_id, {"enabled": True})
    return CommandResult(ok=True, message=f"已取消静音课程：{args.course_id}", payload={"subscription": subscription})


def handle_monitor_test_notify(args: argparse.Namespace) -> CommandResult:
    from .monitor.models import CourseUpdateEvent, event_id_for, utc_now
    from .monitor.service import MonitorService

    event = CourseUpdateEvent(
        event_id=event_id_for(
            course_id="TEST",
            resource_type="assignment",
            resource_id="test",
            event_type="assignment.created",
            new_hash="test",
        ),
        event_type="assignment.created",
        course_id="TEST",
        course_name="测试课程",
        semester=None,
        resource_type="assignment",
        resource_id="test",
        resource_title="测试作业",
        source_url=None,
        old_hash=None,
        new_hash="test",
        old_value=None,
        new_value={"title": "测试作业"},
        changed_fields=["created"],
        detected_at=utc_now(),
        severity="normal",
        summary="测试课程 新增作业：测试作业。",
        raw=None,
    )
    service = MonitorService(snapshot_provider=lambda: [])
    service.store.add_events([event])
    deliveries = service._deliver([event])
    return CommandResult(ok=True, message="测试通知已发送。", payload={"event": event.to_dict(), "deliveries": deliveries})


def handle_agent_serve(args: argparse.Namespace) -> CommandResult:
    from .agent.api_server import serve

    serve(host=args.host, port=args.port)
    return None


def handle_agent_mcp(args: argparse.Namespace) -> CommandResult:
    from .agent.mcp_server import run_stdio

    run_stdio()
    return None


def handle_agent_token(args: argparse.Namespace) -> CommandResult:
    from .agent.schemas import generate_token

    token = generate_token()
    return CommandResult(ok=True, message="已生成 agent token。", payload={"token": token})


def handle_auth_login(args: argparse.Namespace) -> CommandResult:
    try:
        saved_account = None
        should_save_account = not args.no_save_account

        if args.account and args.username:
            return CommandResult(
                ok=False,
                message="`--account` 和 `--username` 只能二选一。",
                payload={},
            )
        if args.account and args.password_stdin:
            return CommandResult(
                ok=False,
                message="`--password-stdin` 不能和 `--account` 一起使用；已保存账号会直接从 macOS 钥匙串读取密码。",
                payload={},
            )

        if args.account:
            saved_account = resolve_account(args.account)
            credentials = credentials_for_account(saved_account)
        elif args.username or args.password_stdin:
            credentials = prompt_for_credentials(
                username=args.username,
                password_stdin=args.password_stdin,
            )
        else:
            default_account = get_default_account()
            if default_account is not None:
                saved_account = default_account
                credentials = credentials_for_account(default_account)
            else:
                credentials = prompt_for_credentials()

        artifacts = login_with_playwright(
            credentials=credentials,
            storage_state_path=storage_state_path(),
            headless=not args.show_browser,
            timeout_ms=args.timeout_seconds * 1000,
            login_url=args.login_url,
        )
    except (AuthError, AccountError) as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "login_url": args.login_url,
            },
        )

    account_record = None
    if should_save_account:
        try:
            account_record = upsert_account(
                username=credentials.username,
                password=credentials.password,
                label=args.label or (saved_account.label if saved_account else None),
                make_default=True,
                mark_login=True,
            )
        except AccountError as exc:
            return CommandResult(
                ok=False,
                message=f"登录成功，但保存账号失败：{exc}",
                payload={
                    "login_url": args.login_url,
                    "final_url": artifacts.final_url,
                    "storage_state_path": artifacts.storage_state,
                },
            )

    state = load_session()
    now = utc_now_iso()
    state.configured = True
    state.auth_mode = "browser"
    state.cookie_jar = None
    state.storage_state = artifacts.storage_state
    state.browser_profile = None
    state.login_url = args.login_url
    state.user_display = artifacts.user_display
    state.updated_at = now
    state.created_at = state.created_at or now
    state.last_verified_at = now
    state.authenticated = True
    state.account_username = credentials.username
    state.account_label = account_record.label if account_record is not None else (saved_account.label if saved_account else None)

    path = save_session(state)
    return CommandResult(
        ok=True,
        message=(
            f"浏览器登录成功，并已保存账号 {credentials.username}。"
            if account_record is not None
            else "浏览器登录成功，已保存会话状态。"
        ),
        payload={
            "session_path": str(path),
            "storage_state_path": artifacts.storage_state,
            "accounts_path": str(accounts_path()),
            "credentials_source": credentials.source,
            "final_url": artifacts.final_url,
            "account": _account_payload(account_record) if account_record is not None else None,
            "session": state.to_dict(),
        },
    )


def handle_auth_logout(args: argparse.Namespace) -> CommandResult:
    removed = clear_session()
    return CommandResult(
        ok=True,
        message="已清除本地会话状态。" if removed else "当前没有可清除的本地会话状态。",
        payload={
            "session_path": str(session_path()),
            "storage_state_path": str(storage_state_path()),
            "removed": removed,
        },
    )


def handle_auth_status(args: argparse.Namespace) -> CommandResult:
    state = load_session()
    accounts = list_accounts()
    default_account = next((account for account in accounts if account.is_default), None)
    return CommandResult(
        ok=True,
        message="已读取本地会话状态。" if state.configured else "未找到本地会话状态。",
        payload={
            "session_path": str(session_path()),
            "storage_state_path": str(storage_state_path()),
            "accounts_path": str(accounts_path()),
            "account_count": len(accounts),
            "default_account": _account_payload(default_account) if default_account is not None else None,
            "session": state.to_dict(),
        },
    )


def handle_use_course(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    path = _set_active_course(session, course)
    return CommandResult(
        ok=True,
        message=f"已将活动课程设置为：{course.name}",
        payload={
            "session_path": str(path),
            "course": course.to_dict(),
        },
    )


def handle_current_course(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    if not session.active_course_id and not session.active_course_title:
        return CommandResult(
            ok=False,
            message="当前没有活动课程，请先运行 `pkucw use <course>`。",
            payload={"session": session.to_dict()},
        )

    if session.authenticated and session.storage_state:
        course, _ = _resolve_course_from_query(session, None)
        if course is not None:
            return CommandResult(
                ok=True,
                message=f"已加载当前活动课程：{course.name}",
                payload={
                    "course": course.to_dict(),
                    "session": session.to_dict(),
                },
            )

    return CommandResult(
        ok=True,
        message="已读取本地会话中保存的活动课程。",
        payload={
            "active_course_id": session.active_course_id,
            "active_course_title": session.active_course_title,
            "session": session.to_dict(),
        },
    )


def handle_doctor(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    accounts = list_accounts()
    default_account = next((account for account in accounts if account.is_default), None)
    return CommandResult(
        ok=True,
        message="已收集本地 CLI 诊断信息。",
        payload={
            "installed_commands": {
                "pkucw": True,
                "courseweb": True,
                "cw": True,
            },
            "session_path": str(session_path()),
            "storage_state_path": str(storage_state_path()),
            "accounts_path": str(accounts_path()),
            "account_count": len(accounts),
            "default_account": _account_payload(default_account) if default_account is not None else None,
            "session": session.to_dict(),
            "recommended_flow": [
                "pkucw accounts add",
                "pkucw login",
                "pkucw ls --current",
                "pkucw use <course>",
                "pkucw recordings latest",
            ],
        },
    )


def handle_accounts_list(args: argparse.Namespace) -> CommandResult:
    try:
        accounts = list_accounts()
        payload_accounts = [_account_payload(account) for account in accounts]
    except AccountError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    message = "还没有已保存账号，请先运行 `pkucw accounts add` 或 `pkucw login`。"
    if accounts:
        message = f"已加载 {len(accounts)} 个已保存账号。"
    return CommandResult(
        ok=True,
        message=message,
        payload={
            "accounts_path": str(accounts_path()),
            "count": len(payload_accounts),
            "accounts": payload_accounts,
        },
    )


def handle_accounts_show(args: argparse.Namespace) -> CommandResult:
    try:
        account = get_default_account() if not args.account else resolve_account(args.account)
        if account is None:
            return CommandResult(
                ok=False,
                message="还没有已保存账号，请先运行 `pkucw accounts add`。",
                payload={"accounts_path": str(accounts_path())},
            )
        payload_account = _account_payload(account)
    except AccountError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    return CommandResult(
        ok=True,
        message=f"已读取账号：{account.username}",
        payload={
            "accounts_path": str(accounts_path()),
            "account": payload_account,
        },
    )


def handle_accounts_add(args: argparse.Namespace) -> CommandResult:
    try:
        credentials = prompt_for_credentials(
            username=args.username,
            password_stdin=args.password_stdin,
        )
        account = upsert_account(
            username=credentials.username,
            password=credentials.password,
            label=args.label,
            make_default=args.default,
            mark_login=False,
        )
    except AccountError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    return CommandResult(
        ok=True,
        message=f"已将账号 {account.username} 保存到 macOS Keychain。",
        payload={
            "accounts_path": str(accounts_path()),
            "account": _account_payload(account),
        },
    )


def handle_accounts_use(args: argparse.Namespace) -> CommandResult:
    try:
        account = set_default_account(args.account)
    except AccountError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    session = load_session()
    if session.account_username == account.username:
        session.account_label = account.label
        session.updated_at = utc_now_iso()
        save_session(session)

    return CommandResult(
        ok=True,
        message=f"已将默认账号设置为：{account.username}",
        payload={
            "accounts_path": str(accounts_path()),
            "account": _account_payload(account),
        },
    )


def handle_accounts_remove(args: argparse.Namespace) -> CommandResult:
    try:
        account = remove_account(args.account)
    except AccountError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    session = load_session()
    if session.account_username == account.username:
        session.account_username = None
        session.account_label = None
        session.updated_at = utc_now_iso()
        save_session(session)

    return CommandResult(
        ok=True,
        message=f"已删除账号：{account.username}",
        payload={
            "accounts_path": str(accounts_path()),
            "account": _account_payload(account),
        },
    )


def handle_completion_script(args: argparse.Namespace) -> CommandResult:
    return CommandResult(
        ok=True,
        message=_build_completion_script(args.shell),
        payload={},
    )


def handle_completion_candidates(args: argparse.Namespace) -> CommandResult:
    candidates = _complete_words(build_parser(), args.words)
    return CommandResult(
        ok=True,
        message="\n".join(candidates),
        payload={},
    )


def handle_courses_list(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    try:
        courses = scrape_courses(storage_state_path=session.storage_state or "", headless=True)
    except CourseScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={},
        )

    filtered = courses
    if args.current and not args.archived:
        filtered = [course for course in courses if course.status == "current"]
    elif args.archived and not args.current:
        filtered = [course for course in courses if course.status == "archived"]

    query = (getattr(args, "search", None) or getattr(args, "query", None) or "").strip()
    if query:
        filtered = resolve_course_matches(filtered, query, limit=len(filtered) or 5)

    return CommandResult(
        ok=True,
        message=(
            f"已筛选出 {len(filtered)} 门匹配课程。"
            if query
            else f"已从教学网门户加载 {len(filtered)} 门课程。"
        ),
        payload={
            "count": len(filtered),
            "query": query or None,
            "courses": [course.to_dict() for course in filtered],
        },
    )


def handle_courses_show(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    try:
        courses = scrape_courses(storage_state_path=session.storage_state or "", headless=True)
    except CourseScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={},
        )

    course = resolve_course(courses, args.course)
    if course is None:
        return _course_resolution_error(courses, args.course)

    return CommandResult(
        ok=True,
        message=f"已匹配课程：{course.name}",
        payload={
            "course": course.to_dict(),
        },
    )


def handle_course_info(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info = scrape_course_info(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except CourseScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    return CommandResult(
        ok=True,
        message=f"已加载课程信息：{course.name}",
        payload=info.to_dict(),
    )


def handle_course_assignments_list(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, items = scrape_assignments(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except AssignmentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={},
        )

    return CommandResult(
        ok=True,
        message=f"已加载 {course.name} 的 {len(items)} 条作业条目。",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            "count": len(items),
            "assignments": [item.to_dict() for item in items],
        },
    )


def handle_course_assignments_show(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, items = scrape_assignments(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except AssignmentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={},
        )

    item = resolve_assignment(items, args.assignment)
    if item is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配作业：{args.assignment}",
            payload={
                "query": args.assignment,
                "course": course.to_dict(),
                "available_assignment_count": len(items),
            },
        )

    try:
        detail = scrape_assignment_detail(
            storage_state_path=session.storage_state or "",
            item=item,
            headless=True,
        )
    except AssignmentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "course": course.to_dict(),
                "assignment": item.to_dict(),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已加载作业详情：{item.title}",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            **detail.to_dict(),
        },
    )


def handle_course_assignments_download(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, items = scrape_assignments(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except AssignmentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={},
        )

    item = resolve_assignment(items, args.assignment)
    if item is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配作业：{args.assignment}",
            payload={
                "query": args.assignment,
                "course": course.to_dict(),
                "available_assignment_count": len(items),
            },
        )

    try:
        result = download_assignment(
            storage_state_path=session.storage_state or "",
            item=item,
            output_path=_normalize_output_path(args),
            headless=True,
        )
    except AssignmentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "course": course.to_dict(),
                "assignment": item.to_dict(),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已下载作业内容：{item.title}",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            "download": result.to_dict(),
        },
    )


def handle_course_announcements_list(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, details = scrape_announcements(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except AnnouncementScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    limit = getattr(args, "limit", None)
    if limit is not None and limit >= 0:
        details = details[:limit]

    return CommandResult(
        ok=True,
        message=f"已加载 {course.name} 的 {len(details)} 条通知。",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            "count": len(details),
            "announcements": [detail.item.to_dict() for detail in details],
        },
    )


def handle_course_announcements_show(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, details = scrape_announcements(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except AnnouncementScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    detail = resolve_announcement(details, args.announcement)
    if detail is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配通知：{args.announcement}",
            payload={
                "query": args.announcement,
                "course": course.to_dict(),
                "available_announcement_count": len(details),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已加载通知详情：{detail.item.title}",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            **detail.to_dict(),
        },
    )


def handle_course_contents_list(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, items = scrape_contents(
            storage_state_path=session.storage_state or "",
            course=course,
            recursive=False,
            headless=True,
        )
    except ContentScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    return CommandResult(
        ok=True,
        message=f"已加载 {course.name} 的 {len(items)} 个顶层教学内容。",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            "count": len(items),
            "contents": [item.to_dict() for item in items],
        },
    )


def handle_course_contents_tree(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, items = scrape_contents(
            storage_state_path=session.storage_state or "",
            course=course,
            recursive=True,
            headless=True,
            timeout_ms=60000,
        )
    except ContentScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    return CommandResult(
        ok=True,
        message=f"已加载 {course.name} 的 {len(items)} 个教学内容节点。",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            "count": len(items),
            "contents": [item.to_dict() for item in items],
        },
    )


def handle_course_contents_show(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    info, items, item, content_error = _resolve_content_from_query(
        session,
        course,
        args.content,
    )
    if content_error is not None:
        return content_error
    if item is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配教学内容：{args.content}",
            payload={
                "query": args.content,
                "course": course.to_dict(),
                "available_content_count": len(items),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已加载教学内容详情：{item.title}",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            "content": item.to_dict(),
        },
    )


def handle_course_contents_download(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    _, items, item, content_error = _resolve_content_from_query(
        session,
        course,
        args.content,
    )
    if content_error is not None:
        return content_error
    if item is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配教学内容：{args.content}",
            payload={
                "query": args.content,
                "course": course.to_dict(),
                "available_content_count": len(items),
            },
        )

    try:
        result = download_content(
            storage_state_path=session.storage_state or "",
            item=item,
            output_path=_normalize_output_path(args),
        )
    except ContentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "course": course.to_dict(),
                "content": item.to_dict(),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已下载教学内容：{item.title}",
        payload={
            "course": course.to_dict(),
            "download": result.to_dict(),
        },
    )


def handle_course_recordings_list(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, items = scrape_recordings(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except RecordingScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    return CommandResult(
        ok=True,
        message=f"已加载 {course.name} 的 {len(items)} 条课堂实录。",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            "count": len(items),
            "recordings": [item.to_dict() for item in items],
        },
    )


def handle_course_recordings_show(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        info, items = scrape_recordings(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except RecordingScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    item = resolve_recording(items, args.recording)
    if item is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配课堂实录：{args.recording}",
            payload={
                "query": args.recording,
                "course": course.to_dict(),
                "available_recording_count": len(items),
            },
        )

    try:
        detail = scrape_recording_detail(
            storage_state_path=session.storage_state or "",
            item=item,
            headless=True,
            timeout_ms=45000,
        )
    except RecordingScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "course": course.to_dict(),
                "recording": item.to_dict(),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已加载课堂实录详情：{item.title}",
        payload={
            "course": course.to_dict(),
            "course_page": {
                "page_title": info.page_title,
                "current_page_url": info.current_page_url,
                "current_page_label": info.current_page_label,
            },
            **detail.to_dict(),
        },
    )


def handle_course_recordings_download(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        _, items = scrape_recordings(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except RecordingScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    item = resolve_recording(items, args.recording)
    if item is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配课堂实录：{args.recording}",
            payload={
                "query": args.recording,
                "course": course.to_dict(),
                "available_recording_count": len(items),
            },
        )

    try:
        result = download_recording(
            storage_state_path=session.storage_state or "",
            item=item,
            output_path=_normalize_output_path(args),
            headless=True,
            timeout_ms=45000,
            remux_to_mp4=not args.no_remux,
            show_progress=not args.no_progress,
        )
    except RecordingScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "course": course.to_dict(),
                "recording": item.to_dict(),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已下载课堂实录：{item.title}",
        payload={
            "course": course.to_dict(),
            "download": result.to_dict(),
        },
    )


def handle_course_recordings_download_latest(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        _, items = scrape_recordings(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except RecordingScrapeError as exc:
        return CommandResult(ok=False, message=str(exc), payload={})

    if not items:
        return CommandResult(
            ok=False,
            message=f"{course.name} 当前没有可用的课堂实录。",
            payload={"course": course.to_dict()},
        )

    item = max(items, key=lambda current: current.recorded_at or "")

    try:
        result = download_recording(
            storage_state_path=session.storage_state or "",
            item=item,
            output_path=_normalize_output_path(args),
            headless=True,
            timeout_ms=45000,
            remux_to_mp4=not args.no_remux,
            show_progress=not args.no_progress,
        )
    except RecordingScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "course": course.to_dict(),
                "recording": item.to_dict(),
            },
        )

    return CommandResult(
        ok=True,
        message=f"已下载 {course.name} 的最新课堂实录。",
        payload={
            "course": course.to_dict(),
            "download": result.to_dict(),
        },
    )


def handle_assignment_submit(args: argparse.Namespace) -> CommandResult:
    session = load_session()
    auth_error = _require_authenticated_session(session)
    if auth_error is not None:
        return auth_error

    file_list = [str(item.expanduser().resolve()) for item in args.file]

    if args.save_draft and args.final_submit:
        return CommandResult(
            ok=False,
            message="`--save-draft` 和 `--final-submit` 只能二选一。",
            payload={
                "course": _course_query(args),
                "assignment": args.assignment,
            },
        )

    if args.confirm_final_submit and not args.final_submit:
        return CommandResult(
            ok=False,
            message="`--confirm-final-submit` 只能和 `--final-submit` 一起使用。",
            payload={
                "course": _course_query(args),
                "assignment": args.assignment,
            },
        )

    if args.replace_files and not file_list:
        return CommandResult(
            ok=False,
            message="`--replace-files` 至少需要一个 `--file`。",
            payload={
                "course": _course_query(args),
                "assignment": args.assignment,
            },
        )

    if args.replace_files and args.clear_files:
        return CommandResult(
            ok=False,
            message="文件处理模式只能二选一：`--replace-files` 或 `--clear-files`。",
            payload={
                "course": _course_query(args),
                "assignment": args.assignment,
            },
        )

    if args.text and args.clear_text:
        return CommandResult(
            ok=False,
            message="文本模式只能二选一：`--text` 或 `--clear-text`。",
            payload={
                "course": _course_query(args),
                "assignment": args.assignment,
            },
        )

    if args.comment and args.clear_comment:
        return CommandResult(
            ok=False,
            message="备注模式只能二选一：`--comment` 或 `--clear-comment`。",
            payload={
                "course": _course_query(args),
                "assignment": args.assignment,
            },
        )

    clear_existing_files = args.clear_files or args.replace_files

    has_mutation_input = bool(
        file_list
        or args.text
        or args.comment
        or clear_existing_files
        or args.clear_text
        or args.clear_comment
    )

    if not has_mutation_input and not args.final_submit:
        return CommandResult(
            ok=False,
            message="作业提交至少需要提供 `--file`、`--text` 或 `--comment` 之一。",
            payload={
                "course": _course_query(args),
                "assignment": args.assignment,
            },
        )

    course, error = _resolve_course_from_query(session, _course_query(args))
    if error is not None:
        return error

    try:
        _, items = scrape_assignments(
            storage_state_path=session.storage_state or "",
            course=course,
            headless=True,
        )
    except AssignmentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={},
        )

    item = resolve_assignment(items, args.assignment)
    if item is None:
        return CommandResult(
            ok=False,
            message=f"无法匹配作业：{args.assignment}",
            payload={
                "query": args.assignment,
                "course": course.to_dict(),
                "available_assignment_count": len(items),
            },
        )

    live_action = None
    if args.save_draft:
        live_action = "save"
    elif args.final_submit:
        live_action = "submit"

    if live_action is None:
        return CommandResult(
            ok=True,
            message="已准备好作业提交参数，目前是 dry-run 模式，不会执行真实写入。",
            payload={
                "course": course.to_dict(),
                "assignment": item.to_dict(),
                "text_submission": bool(args.text),
                "attached_files": file_list,
                "replace_files": args.replace_files,
                "clear_files": args.clear_files,
                "clear_text": args.clear_text,
                "clear_comment": args.clear_comment,
                "comment": args.comment,
                "dry_run": True,
                "recommended_next_step": "如需真实保存草稿，请重新运行并加上 `--save-draft`；如需最终提交，请使用 `--final-submit`。",
            },
        )

    if live_action == "submit":
        confirmation = (args.confirm_final_submit or "").strip()
        valid_confirmations = {value for value in (item.id, item.title) if value}
        if confirmation not in valid_confirmations:
            return CommandResult(
                ok=False,
                message="最终提交受保护。请重新运行，并让 `--confirm-final-submit` 与作业 ID 或标题完全一致。",
                payload={
                    "course": course.to_dict(),
                    "assignment": item.to_dict(),
                    "required_confirmations": sorted(valid_confirmations),
                },
            )

    try:
        result = submit_assignment(
            storage_state_path=session.storage_state or "",
            item=item,
            text=args.text,
            comment=args.comment,
            files=file_list,
            clear_existing_files=clear_existing_files,
            clear_text=args.clear_text,
            clear_comment=args.clear_comment,
            action=live_action,
            headless=True,
        )
    except AssignmentScrapeError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "course": course.to_dict(),
                "assignment": item.to_dict(),
                "requested_action": live_action,
            },
        )

    return CommandResult(
        ok=result.ok,
        message=(
            "真实草稿保存已完成。"
            if live_action == "save" and result.ok
            else "真实最终提交已完成。"
            if live_action == "submit" and result.ok
            else "真实作业操作已完成，但带有警告。"
        ),
        payload={
            "course": course.to_dict(),
            "submission": result.to_dict(),
            "text_submission": bool(args.text),
            "attached_files": file_list,
            "replace_files": args.replace_files,
            "clear_files": args.clear_files,
            "clear_text": args.clear_text,
            "clear_comment": args.clear_comment,
            "comment": args.comment,
        },
    )


def make_placeholder_handler(command_name: str, steps: list[str]):
    def handler(args: argparse.Namespace) -> CommandResult:
        session = load_session()
        payload = {
            "command": command_name,
            "prototype": True,
            "session_configured": session.configured,
            "next_steps": steps,
            "args": _namespace_to_dict(args),
        }
        return CommandResult(
            ok=True,
            message=f"{command_name} 已完成命令骨架，但尚未接入真实后端。",
            payload=payload,
        )

    return handler


def _resource_label(name: str) -> str:
    mapping = {
        "announcements": "课程通知",
        "contents": "教学内容",
        "assignments": "课程作业",
        "recordings": "课堂实录",
    }
    return mapping.get(name, name)


def _resource_singular_label(name: str) -> str:
    mapping = {
        "announcements": "通知",
        "contents": "教学内容条目",
        "assignments": "作业",
        "recordings": "课堂实录",
    }
    return mapping.get(name, name)


def _resource_plan_steps(name: str, action: str) -> list[str]:
    if name == "announcements":
        if action == "list":
            return [
                "打开已解析课程下的通知页面。",
                "解析通知标题、发布时间和详情链接。",
            ]
        return [
            "定位到选中的通知条目。",
            "返回完整通知正文和元数据。",
        ]

    if name == "assignments":
        if action == "list":
            return [
                "打开课程作业页面。",
                "区分 Blackboard 原生作业、外部作业和纯附件作业条目。",
            ]
        return [
            "定位到选中的作业条目。",
            "返回截止时间、分值、提交方式和当前状态。",
        ]

    return [f"实现 {name} {action} 的真实后端逻辑。"]


def _namespace_to_dict(args: argparse.Namespace) -> dict[str, object]:
    return {
        key: _normalize_value(value)
        for key, value in vars(args).items()
        if key not in {"handler", "json"}
    }


def _normalize_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value.expanduser().resolve())
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def _scrub_sensitive_message(message: str) -> str:
    session = load_session()
    values = [
        session.account_username,
        session.account_label,
        session.storage_state,
        str(storage_state_path()),
        str(session_path()),
    ]
    scrubbed = message
    for value in values:
        if value:
            scrubbed = scrubbed.replace(str(value), "<redacted>")
    return scrubbed


def _resolve_course_from_query(
    session: SessionState,
    query: str | None,
) -> tuple[CourseRecord | None, CommandResult | None]:
    try:
        courses = scrape_courses(storage_state_path=session.storage_state or "", headless=True)
    except CourseScrapeError as exc:
        return None, CommandResult(
            ok=False,
            message=str(exc),
            payload={},
        )

    effective_query = (query or "").strip() or session.active_course_id or session.active_course_title
    if not effective_query:
        return None, CommandResult(
            ok=False,
            message="既没有传入课程参数，也没有设置活动课程。请先运行 `pkucw use <course>`。",
            payload={
                "available_course_count": len(courses),
                "session": session.to_dict(),
            },
        )

    course = resolve_course(courses, effective_query)
    if course is None:
        return None, _course_resolution_error(courses, effective_query)
    return course, None


def _set_active_course(session: SessionState, course: CourseRecord):
    session.active_course_id = course.id
    session.active_course_title = course.title
    session.updated_at = utc_now_iso()
    return save_session(session)


def _course_resolution_error(courses: list[CourseRecord], query: str) -> CommandResult:
    suggestions = suggest_courses(courses, query, limit=5)
    payload = {
        "query": query,
        "available_course_count": len(courses),
    }
    if suggestions:
        payload["suggestions"] = [course.to_dict() for course in suggestions]
        return CommandResult(
            ok=False,
            message=f"无法匹配课程：{query}。请尝试下面的候选项。",
            payload=payload,
        )
    return CommandResult(
        ok=False,
        message=f"无法匹配课程：{query}",
        payload=payload,
    )


def _account_payload(account) -> dict[str, object] | None:
    if account is None:
        return None
    payload = account.to_dict()
    try:
        payload["has_saved_password"] = has_saved_password(account)
    except AccountError:
        payload["has_saved_password"] = False
    return payload


def _resolve_content_from_query(
    session: SessionState,
    course: CourseRecord,
    query: str,
) -> tuple[CourseInfo | None, list[ContentItem], ContentItem | None, CommandResult | None]:
    try:
        info, top_level_items = scrape_contents(
            storage_state_path=session.storage_state or "",
            course=course,
            recursive=False,
            headless=True,
            timeout_ms=30000,
        )
    except ContentScrapeError as exc:
        return None, [], None, CommandResult(ok=False, message=str(exc), payload={})

    item = resolve_content(top_level_items, query)
    if item is not None:
        return info, top_level_items, item, None

    try:
        info, recursive_items = scrape_contents(
            storage_state_path=session.storage_state or "",
            course=course,
            recursive=True,
            headless=True,
            timeout_ms=60000,
        )
    except ContentScrapeError as exc:
        return None, [], None, CommandResult(ok=False, message=str(exc), payload={})

    item = resolve_content(recursive_items, query)
    return info, recursive_items, item, None


def _require_authenticated_session(session: SessionState) -> CommandResult | None:
    try:
        ensure_live_session(session, stale_after_seconds=0)
    except SessionRecoveryError as exc:
        return CommandResult(
            ok=False,
            message=str(exc),
            payload={
                "session_path": str(session_path()),
                "storage_state_path": str(storage_state_path()),
            },
        )
    return None


def _build_completion_script(shell: str) -> str:
    if shell == "bash":
        return """_pkucw_completion() {
  local IFS=$'\\n'
  local current="${COMP_WORDS[COMP_CWORD]}"
  COMPREPLY=($(pkucw __complete -- "${COMP_WORDS[@]:1:COMP_CWORD}" "$current"))
}
complete -o default -F _pkucw_completion pkucw
"""

    if shell == "zsh":
        return """autoload -Uz compinit >/dev/null 2>&1
if ! whence compdef >/dev/null 2>&1; then
  compinit -C >/dev/null 2>&1 || true
fi

_pkucw_completion() {
  local -a completions
  completions=("${(@f)$(pkucw __complete -- "${words[@]:2}")}")
  _describe 'pkucw values' completions
}
if whence compdef >/dev/null 2>&1; then
  compdef _pkucw_completion pkucw
fi
"""

    return """function __pkucw_complete
    set -l tokens (commandline -opc)
    set -e tokens[1]
    pkucw __complete -- $tokens
end
complete -c pkucw -f -a "(__pkucw_complete)"
"""


def _complete_words(
    parser: argparse.ArgumentParser,
    words: list[str],
) -> list[str]:
    current_parser, prefix, consumed_positionals = _resolve_completion_context(parser, words)
    suggestions: list[str] = []

    subparsers = _get_subparsers(current_parser)
    if prefix.startswith("-"):
        suggestions.extend(_collect_option_strings(current_parser))
    else:
        if subparsers is not None:
            suggestions.extend(subparsers.choices.keys())
        suggestions.extend(_dynamic_completion_candidates(current_parser, consumed_positionals))
        suggestions.extend(_collect_option_strings(current_parser))

    filtered = [item for item in suggestions if item.startswith(prefix)]
    return list(dict.fromkeys(filtered))


def _resolve_completion_context(
    parser: argparse.ArgumentParser,
    words: list[str],
) -> tuple[argparse.ArgumentParser, str, list[str]]:
    current_parser = parser
    tokens = list(words)
    prefix = tokens[-1] if tokens else ""
    consumed = tokens[:-1] if tokens else []
    consumed_positionals: list[str] = []

    for token in consumed:
        if not token or token.startswith("-"):
            continue
        subparsers = _get_subparsers(current_parser)
        if subparsers is None or token not in subparsers.choices:
            consumed_positionals.append(token)
            continue
        current_parser = subparsers.choices[token]

    return current_parser, prefix, consumed_positionals


def _get_subparsers(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser] | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _collect_option_strings(parser: argparse.ArgumentParser) -> list[str]:
    options: list[str] = []
    for action in parser._actions:
        options.extend(action.option_strings)
    return [item for item in options if item]


def _dynamic_completion_candidates(
    parser: argparse.ArgumentParser,
    consumed_positionals: list[str],
) -> list[str]:
    next_dest = _next_positional_dest(parser, consumed_positionals)
    if next_dest == "course":
        return _course_completion_candidates()
    if next_dest == "account":
        return _account_completion_candidates()
    return []


def _next_positional_dest(parser: argparse.ArgumentParser, consumed_positionals: list[str]) -> str | None:
    positionals = [
        action
        for action in parser._actions
        if not action.option_strings and not isinstance(action, argparse._SubParsersAction)
    ]
    if not positionals:
        return None

    index = min(len(consumed_positionals), len(positionals) - 1)
    return positionals[index].dest


def _course_completion_candidates() -> list[str]:
    session = load_session()
    suggestions: list[str] = []
    if session.active_course_title:
        suggestions.append(session.active_course_title)
    if session.active_course_id:
        suggestions.append(session.active_course_id)

    if session.configured and session.authenticated and session.storage_state:
        try:
            courses = scrape_courses(storage_state_path=session.storage_state, headless=True, timeout_ms=15000)
        except CourseScrapeError:
            courses = []
        for course in courses:
            suggestions.append(course.name)
            suggestions.append(course.title)
            if course.id:
                suggestions.append(course.id)

    return [item for item in dict.fromkeys(suggestions) if item]


def _account_completion_candidates() -> list[str]:
    suggestions: list[str] = []
    for account in list_accounts():
        suggestions.append(account.username)
        if account.label:
            suggestions.append(account.label)
    return [item for item in dict.fromkeys(suggestions) if item]
