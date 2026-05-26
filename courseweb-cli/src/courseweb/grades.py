from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .courses import CourseInfo, CourseRecord, CourseScrapeError, scrape_course_info


class GradeScrapeError(RuntimeError):
    """Raised when grade scraping cannot complete."""


@dataclass(slots=True)
class GradeItem:
    id: str | None
    title: str
    score: str | None
    max_score: str | None
    published_at: str | None
    url: str | None
    course_id: str | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "score": self.score,
            "max_score": self.max_score,
            "published_at": self.published_at,
            "url": self.url,
            "course_id": self.course_id,
            "raw": self.raw,
        }


def scrape_grades(
    *,
    storage_state_path: str,
    course: CourseRecord,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> tuple[CourseInfo, list[GradeItem]]:
    try:
        info = scrape_course_info(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    except CourseScrapeError as exc:
        raise GradeScrapeError(str(exc)) from exc

    grade_menu = _find_grade_menu(info)
    if grade_menu is None or not grade_menu.get("url"):
        return info, []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            page.goto(grade_menu["url"], wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("body", state="attached", timeout=timeout_ms)
            raw_items = page.evaluate(
                """
                () => {
                  const readText = (node) => (node?.textContent || '').replace(/\\s+/g, ' ').trim();
                  const rows = [...document.querySelectorAll('table tr')];
                  const items = [];
                  for (const row of rows) {
                    const cells = [...row.querySelectorAll('th, td')].map((cell) => readText(cell));
                    if (cells.length < 2) continue;
                    const joined = cells.join(' ');
                    if (/成绩项目|Grade Item|名称|分数/i.test(joined) && /总分|possible|满分/i.test(joined)) {
                      continue;
                    }
                    const link = row.querySelector('a[href]');
                    items.push({
                      id: row.id || link?.getAttribute('href') || cells[0],
                      title: cells[0],
                      cells,
                      url: link ? new URL(link.getAttribute('href'), window.location.href).href : null,
                    });
                  }
                  return items;
                }
                """
            )
            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise GradeScrapeError(f"加载课程成绩页面超时：{exc}") from exc
    except Exception as exc:  # pragma: no cover - operational fallback
        raise GradeScrapeError(f"抓取课程成绩失败：{exc}") from exc

    grades: list[GradeItem] = []
    for item in raw_items:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        score, max_score = _parse_score_cells(item.get("cells") or [])
        grades.append(
            GradeItem(
                id=_stable_grade_id(item.get("id"), title),
                title=title,
                score=score,
                max_score=max_score,
                published_at=_parse_published_at(item.get("cells") or []),
                url=item.get("url"),
                course_id=course.id,
                raw=item,
            )
        )
    return info, grades


def resolve_grade(items: list[GradeItem], needle: str) -> GradeItem | None:
    raw = needle.strip().lower()
    if not raw:
        return None
    for item in items:
        if item.id and raw == item.id.lower():
            return item
    for item in items:
        if raw in item.title.lower():
            return item
    return None


def _find_grade_menu(info: CourseInfo) -> dict[str, str | None] | None:
    for item in info.menu_items:
        label = (item.get("label") or "").strip()
        kind = item.get("kind")
        if kind == "grades" or "成绩" in label or "分数" in label or "我的成绩" in label:
            return item
    return None


def _stable_grade_id(raw_id: Any, title: str) -> str:
    raw = str(raw_id or "").strip()
    if raw and raw != title:
        return raw[:160]
    return re.sub(r"\s+", "-", title.strip().lower())[:160]


SCORE_RE = re.compile(r"(?P<score>[0-9]+(?:\.[0-9]+)?)\s*/\s*(?P<max>[0-9]+(?:\.[0-9]+)?)")


def _parse_score_cells(cells: list[str]) -> tuple[str | None, str | None]:
    for cell in cells[1:]:
        match = SCORE_RE.search(cell)
        if match:
            return match.group("score"), match.group("max")
    numeric = [cell for cell in cells[1:] if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cell)]
    if len(numeric) >= 2:
        return numeric[0], numeric[1]
    if numeric:
        return numeric[0], None
    return None, None


DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")


def _parse_published_at(cells: list[str]) -> str | None:
    for cell in cells:
        match = DATE_RE.search(cell)
        if match:
            return match.group(0).replace("/", "-")
    return None
