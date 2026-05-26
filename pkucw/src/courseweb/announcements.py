from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .courses import CourseInfo, CourseRecord, CourseScrapeError, scrape_course_info


class AnnouncementScrapeError(RuntimeError):
    """Raised when announcement scraping cannot complete."""


@dataclass(slots=True)
class AnnouncementItem:
    id: str | None
    title: str
    published_at: str | None
    author: str | None
    posted_to: str | None
    body_preview: str | None
    asset_urls: list[str]
    course_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "published_at": self.published_at,
            "author": self.author,
            "posted_to": self.posted_to,
            "body_preview": self.body_preview,
            "asset_urls": self.asset_urls,
            "course_id": self.course_id,
        }


@dataclass(slots=True)
class AnnouncementDetail:
    item: AnnouncementItem
    body_text: str
    body_html: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "announcement": self.item.to_dict(),
            "body_text": self.body_text,
            "body_html": self.body_html,
        }


def scrape_announcements(
    *,
    storage_state_path: str,
    course: CourseRecord,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> tuple[CourseInfo, list[AnnouncementDetail]]:
    try:
        info = scrape_course_info(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    except CourseScrapeError as exc:
        raise AnnouncementScrapeError(str(exc)) from exc

    announcement_menu = _find_announcement_menu(info)
    if announcement_menu is None:
        return info, []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            page.goto(announcement_menu["url"], wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("#announcementList, body", state="attached", timeout=timeout_ms)

            raw_items = page.evaluate(
                """
                () => {
                  const readText = (node) => (node?.textContent || '').replace(/\\s+/g, ' ').trim();
                  return [...document.querySelectorAll('#announcementList > li.clearfix')].map((row) => {
                    const title = readText(row.querySelector('h3.item'));
                    const details = row.querySelector('.details');
                    const info = row.querySelector('.announcementInfo');
                    const publishedText = readText(details?.querySelector('p span'));
                    const bodyNode = details?.querySelector('.vtbegenerated') || details;
                    const assetUrls = [
                      ...new Set(
                        [...row.querySelectorAll('.details a, .details img')]
                          .map((node) => node.href || node.src || '')
                          .filter(Boolean)
                      ),
                    ];
                    return {
                      id: row.id || null,
                      title,
                      published_at: publishedText.replace(/^发布时间:\\s*/, '') || null,
                      author: readText(info?.querySelector('p:nth-of-type(1)')).replace(/^发帖者:\\s*/, '') || null,
                      posted_to: readText(info?.querySelector('p:nth-of-type(2)')).replace(/^发布至:\\s*/, '') || null,
                      body_preview: readText(bodyNode).slice(0, 280) || null,
                      body_text: readText(bodyNode),
                      body_html: bodyNode ? bodyNode.innerHTML.trim() : '',
                      asset_urls: assetUrls,
                    };
                  });
                }
                """
            )

            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise AnnouncementScrapeError(f"加载课程通知超时：{exc}") from exc
    except Exception as exc:  # pragma: no cover - operational fallback
        raise AnnouncementScrapeError(f"抓取课程通知失败：{exc}") from exc

    details = [
        AnnouncementDetail(
            item=AnnouncementItem(
                id=item["id"],
                title=item["title"],
                published_at=item["published_at"],
                author=item["author"],
                posted_to=item["posted_to"],
                body_preview=item["body_preview"],
                asset_urls=item["asset_urls"],
                course_id=course.id,
            ),
            body_text=item["body_text"],
            body_html=item["body_html"],
        )
        for item in raw_items
        if item["title"]
    ]
    return info, details


def resolve_announcement(items: list[AnnouncementDetail], needle: str) -> AnnouncementDetail | None:
    raw = needle.strip().lower()
    if not raw:
        return None

    for item in items:
        if item.item.id and raw == item.item.id.lower():
            return item

    for item in items:
        if raw in item.item.title.lower():
            return item

    return None


def _find_announcement_menu(info: CourseInfo) -> dict[str, str | None] | None:
    for item in info.menu_items:
        label = (item.get("label") or "").strip()
        kind = item.get("kind")
        if kind == "announcements" or "通知" in label or "公告" in label:
            return item
    return None
