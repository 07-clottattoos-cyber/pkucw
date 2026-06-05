from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_LOGIN_URL = "https://course.pku.edu.cn/webapps/bb-sso-BBLEARN/login.html"
PORTAL_URL_RE = re.compile(r"https://course\.pku\.edu\.cn/webapps/portal/execute/tabs/tabAction")
PORTAL_HOME_URL = "https://course.pku.edu.cn/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"


class AuthError(RuntimeError):
    """Raised when real auth login cannot complete."""


@dataclass(slots=True)
class Credentials:
    username: str
    password: str
    source: str


@dataclass(slots=True)
class LoginArtifacts:
    storage_state: str
    final_url: str
    user_display: str | None


def login_with_playwright(
    *,
    credentials: Credentials,
    storage_state_path: Path,
    headless: bool,
    timeout_ms: int,
    login_url: str = DEFAULT_LOGIN_URL,
) -> LoginArtifacts:
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            _open_login_entry(page, entry_urls=[login_url, PORTAL_HOME_URL], timeout_ms=timeout_ms)
            if not PORTAL_URL_RE.search(page.url):
                _wait_for_login_surface(page, timeout_ms=timeout_ms)
            if not PORTAL_URL_RE.search(page.url):
                _fill_first_available(
                    page,
                    selectors=[
                        "#user_name",
                        'input[name="userName"]',
                        'input[placeholder="学号/职工号/手机号"]',
                        'input[placeholder="User ID / PKU Email / Cell Phone"]',
                    ],
                    value=credentials.username,
                    timeout_ms=timeout_ms,
                    field_label="username",
                )
                _fill_first_available(
                    page,
                    selectors=[
                        "#password",
                        'input[name="password"]',
                        'input[placeholder="密码"]',
                        'input[placeholder="Password"]',
                    ],
                    value=credentials.password,
                    timeout_ms=timeout_ms,
                    field_label="password",
                )
                page.locator("#logon_button").click()

                _wait_for_portal_surface(page, timeout_ms=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 4000))
            except PlaywrightTimeoutError:
                pass
            try:
                page.wait_for_selector("main h1", timeout=5000)
            except PlaywrightTimeoutError:
                pass

            user_display = _extract_user_display(page)
            context.storage_state(path=str(storage_state_path))
            final_url = page.url

            context.close()
            browser.close()
            return LoginArtifacts(
                storage_state=str(storage_state_path),
                final_url=final_url,
                user_display=user_display,
            )
    except PlaywrightTimeoutError as exc:
        raise AuthError(f"浏览器登录超时：{exc}") from exc
    except Exception as exc:  # pragma: no cover - used for operational errors
        raise AuthError(f"浏览器登录失败：{exc}") from exc


def _fill_first_available(page, *, selectors: list[str], value: str, timeout_ms: int, field_label: str) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=min(timeout_ms, 10000))
        except PlaywrightTimeoutError:
            continue
        locator.fill(value)
        return
    field_name = "用户名" if field_label == "username" else "密码" if field_label == "password" else field_label
    raise AuthError(f"登录页面中找不到“{field_name}”输入框。")


def _wait_for_login_surface(page, *, timeout_ms: int) -> None:
    selectors = [
        "#user_name",
        'input[name="userName"]',
        'input[placeholder="学号/职工号/手机号"]',
        'input[placeholder="User ID / PKU Email / Cell Phone"]',
        "#password",
        'input[name="password"]',
    ]
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        if PORTAL_URL_RE.search(page.url):
            return
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count():
                try:
                    locator.wait_for(state="visible", timeout=250)
                except PlaywrightTimeoutError:
                    continue
                return
        page.wait_for_timeout(250)
    raise AuthError("未能进入北大统一登录表单页面。")


def _wait_for_portal_surface(page, *, timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        if PORTAL_URL_RE.search(page.url):
            return
        if page.locator("a[href*='execute/launcher?type=Course']").count():
            return
        page.wait_for_timeout(250)
    raise AuthError("无法确认是否已成功进入教学网门户。")


def _open_login_entry(page, *, entry_urls: list[str], timeout_ms: int) -> None:
    unique_urls: list[str] = []
    for url in entry_urls:
        if url and url not in unique_urls:
            unique_urls.append(url)

    last_error: PlaywrightTimeoutError | None = None
    per_url_timeout = max(3000, timeout_ms // max(len(unique_urls), 1))
    for entry_url in unique_urls:
        try:
            page.goto(entry_url, wait_until="commit", timeout=per_url_timeout)
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
    raise last_error or AuthError("无法打开任何一个北大登录入口 URL。")


def _extract_user_display(page) -> str | None:
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
