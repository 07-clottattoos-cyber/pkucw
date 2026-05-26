from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .accounts import AccountError, credentials_for_account, get_default_account, resolve_account
from .auth import DEFAULT_LOGIN_URL, PORTAL_URL_RE, AuthError, login_with_playwright
from .models import SessionState
from .state import save_session, storage_state_path, utc_now_iso


PORTAL_HOME_URL = "https://course.pku.edu.cn/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"
LOGIN_URL_SNIPPETS = (
    "iaaa.pku.edu.cn",
    "/webapps/bb-sso-BBLEARN/login.html",
)


class SessionRecoveryError(RuntimeError):
    """在已保存会话无法复用或自动恢复时抛出。"""


@dataclass(slots=True)
class SessionProbe:
    authenticated: bool
    final_url: str
    user_display: str | None = None
    indeterminate: bool = False


def ensure_live_session(
    session: SessionState,
    *,
    allow_auto_login: bool = True,
    probe_timeout_ms: int = 2000,
    login_timeout_ms: int = 6000,
    stale_after_seconds: int = 300,
) -> SessionState:
    if _session_looks_recent(session, stale_after_seconds=stale_after_seconds):
        return session

    if session.storage_state and Path(session.storage_state).exists():
        probe: SessionProbe | None = None
        try:
            probe = probe_session(
                storage_state=session.storage_state,
                timeout_ms=probe_timeout_ms,
                headless=True,
            )
        except Exception as exc:
            if not allow_auto_login:
                raise SessionRecoveryError(f"无法校验已保存的会话：{exc}") from exc
        else:
            if probe.authenticated:
                _mark_session_verified(session, user_display=probe.user_display)
                return session
            if not allow_auto_login:
                raise SessionRecoveryError("已保存的会话已失效，请先运行 `pkucw login`。")

            if not allow_auto_login:
                raise SessionRecoveryError("当前没有可用的已登录会话，请先运行 `pkucw login`。")

    try:
        return auto_login_with_saved_account(session, timeout_ms=login_timeout_ms)
    except SessionRecoveryError:
        if probe is not None and probe.indeterminate and session.storage_state and Path(session.storage_state).exists():
            return session
        raise


def auto_login_with_saved_account(session: SessionState, *, timeout_ms: int = 30000) -> SessionState:
    account = _resolve_recovery_account(session)
    if account is None:
        raise SessionRecoveryError(
            "当前没有可用的已登录会话，也没有可用于自动登录的已保存账号。"
            "请先运行 `pkucw accounts add` 或 `pkucw login`。"
        )

    try:
        credentials = credentials_for_account(account)
        artifacts = login_with_playwright(
            credentials=credentials,
            storage_state_path=storage_state_path(),
            headless=True,
            timeout_ms=timeout_ms,
            login_url=session.login_url or DEFAULT_LOGIN_URL,
        )
    except (AccountError, AuthError) as exc:
        raise SessionRecoveryError(f"使用已保存账号 {account.username} 自动登录失败：{exc}") from exc

    now = utc_now_iso()
    session.configured = True
    session.auth_mode = "browser"
    session.cookie_jar = None
    session.storage_state = artifacts.storage_state
    session.browser_profile = None
    session.login_url = session.login_url or DEFAULT_LOGIN_URL
    session.user_display = artifacts.user_display
    session.updated_at = now
    session.created_at = session.created_at or now
    session.last_verified_at = now
    session.authenticated = True
    session.account_username = account.username
    session.account_label = account.label
    save_session(session)
    return session


def probe_session(*, storage_state: str, timeout_ms: int = 8000, headless: bool = True) -> SessionProbe:
    state_path = Path(storage_state).expanduser().resolve()
    if not state_path.exists():
        return SessionProbe(authenticated=False, final_url=str(state_path))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=str(state_path), ignore_https_errors=True)
            page = context.new_page()
            try:
                page.goto(PORTAL_HOME_URL, wait_until="commit", timeout=timeout_ms)
                try:
                    page.wait_for_url(PORTAL_URL_RE, timeout=min(timeout_ms, 2000))
                except PlaywrightTimeoutError:
                    pass

                final_url = page.url
                user_display = _probe_user_display(page)
                authenticated = bool(PORTAL_URL_RE.search(final_url)) and not _looks_like_login_page(final_url)
                if not authenticated:
                    try:
                        page.wait_for_selector(
                            "a[href*='execute/launcher?type=Course']",
                            state="attached",
                            timeout=min(timeout_ms, 1500),
                        )
                    except PlaywrightTimeoutError:
                        authenticated = False
                    else:
                        authenticated = True
                return SessionProbe(
                    authenticated=authenticated,
                    final_url=final_url,
                    user_display=user_display,
                )
            finally:
                context.close()
                browser.close()
    except PlaywrightTimeoutError:
        return SessionProbe(authenticated=False, final_url=PORTAL_HOME_URL, indeterminate=True)


def _session_looks_recent(session: SessionState, *, stale_after_seconds: int) -> bool:
    if not session.configured or not session.authenticated or not session.storage_state:
        return False
    timestamp = (session.last_verified_at or "").strip()
    if not timestamp:
        return False
    try:
        last_verified = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last_verified <= timedelta(seconds=stale_after_seconds)


def _mark_session_verified(session: SessionState, *, user_display: str | None) -> None:
    now = utc_now_iso()
    session.configured = True
    session.authenticated = True
    session.last_verified_at = now
    session.updated_at = now
    if user_display:
        session.user_display = user_display
    save_session(session)


def _resolve_recovery_account(session: SessionState):
    try:
        if session.account_username:
            return resolve_account(session.account_username)
    except AccountError:
        pass
    return get_default_account()


def _looks_like_login_page(url: str) -> bool:
    return any(snippet in url for snippet in LOGIN_URL_SNIPPETS)


def _probe_user_display(page) -> str | None:
    selectors = [
        "main h1",
        "button[aria-label*='展开全局导航']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count():
            text = locator.inner_text().strip()
            if text.startswith("欢迎，"):
                return text.removeprefix("欢迎，").strip()
            if text:
                return text.split()[0].strip()
    return None
