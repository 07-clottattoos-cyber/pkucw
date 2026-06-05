from __future__ import annotations

import json
import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .courses import CourseInfo, CourseRecord, CourseScrapeError, scrape_course_info
from .download_utils import resolve_download_destination, safe_download_name


class ContentScrapeError(RuntimeError):
    """Raised when course content scraping cannot complete."""


INVALID_PATH_CHARS_RE = re.compile(r'[\\\\/:*?"<>|]+')


@dataclass(slots=True)
class ContentItem:
    id: str | None
    title: str
    type: str
    url: str
    course_id: str | None
    parent_id: str | None
    path: list[str]
    description: str | None
    download_url: str | None
    asset_urls: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "url": self.url,
            "course_id": self.course_id,
            "parent_id": self.parent_id,
            "path": self.path,
            "description": self.description,
            "download_url": self.download_url,
            "asset_urls": self.asset_urls,
        }


@dataclass(slots=True)
class ContentDownloadResult:
    item: ContentItem
    output_path: str
    downloaded_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.item.to_dict(),
            "output_path": self.output_path,
            "downloaded_files": self.downloaded_files,
        }


def scrape_contents(
    *,
    storage_state_path: str,
    course: CourseRecord,
    recursive: bool = False,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> tuple[CourseInfo, list[ContentItem]]:
    try:
        info = scrape_course_info(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    except CourseScrapeError as exc:
        raise ContentScrapeError(str(exc)) from exc

    content_menu = _find_teaching_content_menu(info)
    if content_menu is None:
        return info, []

    seen_urls: set[str] = set()
    items = _scrape_content_page(
        storage_state_path=storage_state_path,
        course=course,
        page_url=content_menu["url"] or "",
        parent_id=None,
        path=[],
        recursive=recursive,
        seen_urls=seen_urls,
        headless=headless,
        timeout_ms=timeout_ms,
    )
    return info, items


def resolve_content(items: list[ContentItem], needle: str) -> ContentItem | None:
    raw = needle.strip().lower()
    if not raw:
        return None

    for item in items:
        if item.id and raw == item.id.lower():
            return item

    for item in items:
        if raw == " / ".join(part.lower() for part in item.path):
            return item

    for item in items:
        if raw in item.title.lower():
            return item

    return None


def download_content(
    *,
    storage_state_path: str,
    item: ContentItem,
    output_path: str | None = None,
    timeout_seconds: int = 60,
) -> ContentDownloadResult:
    if item.type == "folder":
        target_dir = Path(output_path).expanduser().resolve() if output_path else Path.cwd() / _safe_name(item.title)
        target_dir.mkdir(parents=True, exist_ok=True)
        descendants = _scrape_content_page(
            storage_state_path=storage_state_path,
            course=CourseRecord(
                id=item.course_id,
                title=item.title,
                name=item.title,
                term=None,
                role="student",
                status="current",
                launcher_url=item.url,
            ),
            page_url=item.url,
            parent_id=item.id,
            path=item.path[:-1],
            recursive=True,
            seen_urls=set(),
            headless=True,
            timeout_ms=timeout_seconds * 1000,
        )
        downloaded_files: list[str] = []
        for descendant in descendants:
            if descendant.type != "file" or not descendant.download_url:
                continue
            relative_parts = descendant.path[len(item.path) :]
            destination = target_dir.joinpath(*[_safe_name(part) for part in relative_parts])
            saved_path = _download_file(
                storage_state_path=storage_state_path,
                url=descendant.download_url,
                destination=destination,
                timeout_seconds=timeout_seconds,
            )
            downloaded_files.append(str(saved_path))
        return ContentDownloadResult(
            item=item,
            output_path=str(target_dir),
            downloaded_files=downloaded_files,
        )

    if item.type != "file" or not item.download_url:
        raise ContentScrapeError("当前仅支持下载文件和文件夹类型的教学内容。")

    target_path = Path(output_path).expanduser().resolve() if output_path else Path.cwd() / _safe_name(item.title)
    if target_path.exists() and target_path.is_dir():
        target_path = target_path / _safe_name(item.title)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    saved_path = _download_file(
        storage_state_path=storage_state_path,
        url=item.download_url,
        destination=target_path,
        timeout_seconds=timeout_seconds,
    )
    return ContentDownloadResult(
        item=item,
        output_path=str(saved_path),
        downloaded_files=[str(saved_path)],
    )


def _scrape_content_page(
    *,
    storage_state_path: str,
    course: CourseRecord,
    page_url: str,
    parent_id: str | None,
    path: list[str],
    recursive: bool,
    seen_urls: set[str],
    headless: bool,
    timeout_ms: int,
) -> list[ContentItem]:
    if not page_url:
        return []
    if page_url in seen_urls:
        return []
    seen_urls.add(page_url)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("#content_listContainer, body", state="attached", timeout=timeout_ms)
            raw_items = page.evaluate(
                """
                () => {
                  const readText = (node) => {
                    if (!node) return '';
                    const clone = node.cloneNode(true);
                    clone.querySelectorAll('script, style').forEach((child) => child.remove());
                    return (clone.textContent || '').replace(/\\s+/g, ' ').trim();
                  };
                  const rows = [...document.querySelectorAll('#content_listContainer > li.liItem, #content_listContainer > li')];
                  return rows.map((row) => {
                    const detailsNode = row.querySelector('.details');
                    const itemNode = row.querySelector('.item');
                    const heading = row.querySelector('h3');
                    const anchor = heading?.querySelector('a');
                    const title = readText(heading);
                    const iconAlt = row.querySelector('.item_icon')?.getAttribute('alt') || null;
                    const descriptionNode = detailsNode?.querySelector('.vtbegenerated') || detailsNode;
                    const assetUrls = [
                      ...new Set(
                        [
                          ...[...row.querySelectorAll('.details a, .details video, .details audio, .details img')]
                            .map((node) => node.href || node.src || '')
                            .filter(Boolean),
                          ...[...row.querySelectorAll('.details param[name="Url"], .details param[name="url"]')]
                            .map((node) => node.getAttribute('value') || '')
                            .filter(Boolean),
                        ]
                      ),
                    ];
                    const primaryAssetUrl =
                      assetUrls.find((value) => value.indexOf('/bbcswebdav/') !== -1) ||
                      assetUrls.find((value) => /^https?:/i.test(value)) ||
                      null;
                    return {
                      row_id: row.id || null,
                      item_id: itemNode?.id || null,
                      title,
                      url: anchor?.href || primaryAssetUrl,
                      icon_alt: iconAlt,
                      description: readText(descriptionNode) || null,
                      asset_urls: assetUrls,
                    };
                  }).filter((item) => item.title);
                }
                """
            )
            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise ContentScrapeError(f"加载教学内容超时：{exc}") from exc
    except Exception as exc:  # pragma: no cover - operational fallback
        raise ContentScrapeError(f"抓取教学内容失败：{exc}") from exc

    items: list[ContentItem] = []
    for raw in raw_items:
        item_id = raw["item_id"] or _parse_content_id(raw["row_id"])
        item_type = _infer_content_type(raw["url"], raw["icon_alt"])
        item_path = [*path, raw["title"]]
        item = ContentItem(
            id=item_id,
            title=raw["title"],
            type=item_type,
            url=raw["url"] or page_url,
            course_id=course.id,
            parent_id=parent_id,
            path=item_path,
            description=raw["description"],
            download_url=raw["url"] if item_type == "file" else None,
            asset_urls=raw["asset_urls"],
        )
        items.append(item)

        if recursive and item_type == "folder" and raw["url"]:
            items.extend(
                _scrape_content_page(
                    storage_state_path=storage_state_path,
                    course=course,
                    page_url=raw["url"],
                    parent_id=item.id,
                    path=item_path,
                    recursive=True,
                    seen_urls=seen_urls,
                    headless=headless,
                    timeout_ms=timeout_ms,
                )
            )

    return items


def _find_teaching_content_menu(info: CourseInfo) -> dict[str, str | None] | None:
    for item in info.menu_items:
        label = (item.get("label") or "").strip()
        if label == "教学内容":
            return item
    for item in info.menu_items:
        label = (item.get("label") or "").strip()
        if item.get("kind") == "content" and "作业" not in label:
            return item
    return None


def _infer_content_type(url: str | None, icon_alt: str | None) -> str:
    icon = (icon_alt or "").strip()
    if url and "/listContent.jsp" in url:
        return "folder"
    if url and "/bbcswebdav/" in url:
        return "file"
    if "文件夹" in icon:
        return "folder"
    if "文件" in icon:
        return "file"
    if url and urlparse(url).netloc and urlparse(url).netloc != "course.pku.edu.cn":
        return "external-link"
    if url and "/launchLink.jsp" in url:
        return "tool-link"
    return "content-link"


def _parse_content_id(value: str | None) -> str | None:
    if not value:
        return None
    if ":" in value:
        return value.split(":", 1)[1]
    return value


def _safe_name(value: str) -> str:
    cleaned = INVALID_PATH_CHARS_RE.sub("_", value).strip().rstrip(".")
    return safe_download_name(cleaned)


def _download_file(
    *,
    storage_state_path: str,
    url: str,
    destination: Path,
    timeout_seconds: int,
) -> Path:
    cookies = _cookie_header_for_url(storage_state_path, url)
    headers = {
        "Cookie": cookies,
        "User-Agent": "courseweb-cli/0.1",
    }
    request = Request(url, headers=headers)
    ssl_context = ssl._create_unverified_context()
    with urlopen(request, timeout=timeout_seconds, context=ssl_context) as response:
        content_disposition = response.headers.get("Content-Disposition", "")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        payload = response.read()
        final_destination = resolve_download_destination(
            destination=destination,
            url=url,
            content_disposition=content_disposition,
            content_type=content_type,
            payload=payload,
        )
        final_destination.parent.mkdir(parents=True, exist_ok=True)
        final_destination.write_bytes(payload)
    return final_destination


def _cookie_header_for_url(storage_state_path: str, url: str) -> str:
    data = json.loads(Path(storage_state_path).read_text())
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    cookies = []
    for cookie in data.get("cookies", []):
        domain = str(cookie.get("domain") or "")
        if not host.endswith(domain.lstrip(".")):
            continue
        cookie_path = str(cookie.get("path") or "/")
        if not path.startswith(cookie_path):
            continue
        cookies.append(f"{cookie['name']}={cookie['value']}")
    return "; ".join(cookies)
