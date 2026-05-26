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


class AssignmentScrapeError(RuntimeError):
    """Raised when assignment scraping cannot complete."""


INVALID_PATH_CHARS_RE = re.compile(r'[\\\\/:*?"<>|]+')


@dataclass(slots=True)
class AssignmentAsset:
    label: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "url": self.url,
        }


@dataclass(slots=True)
class AssignmentItem:
    id: str | None
    title: str
    type: str
    url: str
    course_id: str | None
    description: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "url": self.url,
            "course_id": self.course_id,
            "description": self.description,
        }


@dataclass(slots=True)
class AssignmentDetail:
    item: AssignmentItem
    page_title: str | None
    mode: str
    due_at: str | None
    points_possible: str | None
    current_grade: str | None
    supports_text: bool
    supports_file_upload: bool
    supports_library_upload: bool
    supports_comment: bool
    instructions: list[str]
    instructions_html: str | None
    attachments: list[AssignmentAsset]
    submitted_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment": self.item.to_dict(),
            "page_title": self.page_title,
            "mode": self.mode,
            "due_at": self.due_at,
            "points_possible": self.points_possible,
            "current_grade": self.current_grade,
            "supports_text": self.supports_text,
            "supports_file_upload": self.supports_file_upload,
            "supports_library_upload": self.supports_library_upload,
            "supports_comment": self.supports_comment,
            "instructions": self.instructions,
            "instructions_html": self.instructions_html,
            "attachments": [asset.to_dict() for asset in self.attachments],
            "submitted_files": self.submitted_files,
        }


@dataclass(slots=True)
class AssignmentDownloadResult:
    item: AssignmentItem
    output_path: str
    summary_path: str | None
    downloaded_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment": self.item.to_dict(),
            "output_path": self.output_path,
            "summary_path": self.summary_path,
            "downloaded_files": self.downloaded_files,
        }


@dataclass(slots=True)
class AssignmentSubmissionResult:
    item: AssignmentItem
    action: str
    final_url: str
    page_title: str | None
    page_text_excerpt: str
    response_status: int | None
    ok: bool
    note: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment": self.item.to_dict(),
            "action": self.action,
            "final_url": self.final_url,
            "page_title": self.page_title,
            "page_text_excerpt": self.page_text_excerpt,
            "response_status": self.response_status,
            "ok": self.ok,
            "note": self.note,
        }


def scrape_assignments(
    *,
    storage_state_path: str,
    course: CourseRecord,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> tuple[CourseInfo, list[AssignmentItem]]:
    try:
        info = scrape_course_info(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    except CourseScrapeError as exc:
        raise AssignmentScrapeError(str(exc)) from exc

    assignment_menu = _find_assignment_menu(info)
    if assignment_menu is None:
        return info, []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            page.goto(assignment_menu["url"], wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(
                "#content_listContainer, #content",
                state="attached",
                timeout=timeout_ms,
            )

            raw_items = page.evaluate(
                """
                () => {
                  const rows = document.querySelectorAll('#content_listContainer > li.liItem');
                  const items = [];
                  for (let i = 0; i < rows.length; i += 1) {
                    const row = rows[i];
                    const heading = row.querySelector('h3');
                    if (!heading) continue;
                    const anchor = heading.querySelector('a');
                    const title = (heading.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!title) continue;
                    const url = anchor ? anchor.href : null;
                    if (!url) continue;
                    const detailTexts = [];
                    const nodes = row.querySelectorAll('.details, .details p, .details div, .vtbegenerated');
                    for (let j = 0; j < nodes.length; j += 1) {
                      const text = (nodes[j].textContent || '').replace(/\\s+/g, ' ').trim();
                      if (!text) continue;
                      if (text === title) continue;
                      if (detailTexts.indexOf(text) !== -1) continue;
                      detailTexts.push(text);
                    }
                    items.push({
                      title,
                      url,
                      description: detailTexts.length ? detailTexts[0] : null,
                    });
                  }
                  return items;
                }
                """
            )

            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise AssignmentScrapeError(f"加载课程作业页面超时：{exc}") from exc
    except Exception as exc:  # pragma: no cover - operational fallback
        raise AssignmentScrapeError(f"抓取课程作业失败：{exc}") from exc

    items = [
        AssignmentItem(
            id=_parse_identifier(item["url"]),
            title=item["title"],
            type=_infer_assignment_type(item["url"]),
            url=item["url"],
            course_id=course.id,
            description=item.get("description"),
        )
        for item in raw_items
    ]
    return info, items


def scrape_assignment_detail(
    *,
    storage_state_path: str,
    item: AssignmentItem,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> AssignmentDetail:
    if item.type != "blackboard-assignment":
        return AssignmentDetail(
            item=item,
            page_title=None,
            mode="link",
            due_at=None,
            points_possible=None,
            current_grade=None,
            supports_text=False,
            supports_file_upload=False,
            supports_library_upload=False,
            supports_comment=False,
            instructions=[],
            instructions_html=None,
            attachments=[],
            submitted_files=[],
        )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            page.goto(item.url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("body, h1", state="attached", timeout=timeout_ms)

            raw = page.evaluate(
                """
                () => {
                  const readText = (node) => (node?.textContent || '').replace(/\\s+/g, ' ').trim();
                  const pageTitle = document.title;
                  const reviewMode = pageTitle.indexOf('复查提交历史记录') !== -1;

                  const editorMeta = {};
                  const editorMetaSections = document.querySelectorAll('#stepcontent1 .metaSection');
                  for (let i = 0; i < editorMetaSections.length; i += 1) {
                    const label = readText(editorMetaSections[i].querySelector('.metaLabel'));
                    const value = readText(editorMetaSections[i].querySelector('.metaField'));
                    if (label && value) {
                      editorMeta[label] = value;
                    }
                  }

                  const reviewMeta = {};
                  const assignmentInfo = document.querySelector('#assignmentInfo');
                  if (assignmentInfo) {
                    const nodes = assignmentInfo.querySelectorAll('h3, p');
                    let currentLabel = null;
                    for (let i = 0; i < nodes.length; i += 1) {
                      const tag = nodes[i].tagName.toLowerCase();
                      const value = readText(nodes[i]);
                      if (!value) continue;
                      if (tag === 'h3') {
                        currentLabel = value;
                      } else if (tag === 'p' && currentLabel) {
                        reviewMeta[currentLabel] = value;
                        currentLabel = null;
                      }
                    }
                  }

                  const instructionNodes = [];
                  const instructionHtmlNode = reviewMode
                    ? document.querySelector('#contentDetails .vtbegenerated, #contentDetails')
                    : document.querySelector('#instructions .vtbegenerated, #instructions, #stepcontent1 .vtbegenerated');
                  const instructionCandidates = reviewMode
                    ? document.querySelectorAll('#contentDetails .vtbegenerated, #contentDetails p, #contentDetails li')
                    : document.querySelectorAll('#instructions .vtbegenerated, #instructions p, #instructions li, #stepcontent1 .vtbegenerated');
                  for (let i = 0; i < instructionCandidates.length; i += 1) {
                    const snippet = readText(instructionCandidates[i]);
                    if (!snippet) continue;
                    if (instructionNodes.indexOf(snippet) !== -1) continue;
                    instructionNodes.push(snippet);
                  }

                  const attachmentEntries = [];
                  const attachmentCandidates = reviewMode
                    ? document.querySelectorAll('#contentDetails a, #assignmentInfo a, a')
                    : document.querySelectorAll('#instructions a, #stepcontent1 a, a');
                  for (let i = 0; i < attachmentCandidates.length; i += 1) {
                    const node = attachmentCandidates[i];
                    const href = node.href || '';
                    const label = readText(node);
                    if (!href) continue;
                    if (!/bbcswebdav|download|attachment/i.test(href)) continue;
                    if (!label) continue;
                    if (attachmentEntries.find((entry) => entry.href === href)) continue;
                    attachmentEntries.push({ label, href });
                  }

                  const reviewFiles = [...document.querySelectorAll('#currentAttempt_submissionList a')]
                    .map((node) => ({
                      label: readText(node),
                      href: node.href || '',
                    }))
                    .filter((entry) => entry.label)
                    .filter(
                      (entry) =>
                        /\\.(pdf|doc|docx|jpg|png|zip|txt|md)$/i.test(entry.label) ||
                        /bbcswebdav|download|attachment/i.test(entry.href)
                    )
                    .map((entry) => entry.label);
                  const genericFiles = [...document.querySelectorAll('a')]
                    .map(readText)
                    .filter(label => /\\.(pdf|doc|docx|jpg|png|zip|txt|md)$/i.test(label));
                  const submittedFiles = reviewFiles.length ? reviewFiles : genericFiles;

                  const currentGradeInput = document.querySelector('#currentAttempt_grade');
                  const currentGradeValue = readText(currentGradeInput);
                  const pointsPossibleNode = document.querySelector('#currentAttempt_pointsPossible');
                  const pointsPossibleValue = readText(pointsPossibleNode).replace(/^\\//, '');

                  return {
                    page_title: pageTitle,
                    due_at: reviewMode ? (reviewMeta['到期日期'] || null) : (editorMeta['到期日期'] || null),
                    points_possible: reviewMode ? (pointsPossibleValue || null) : (editorMeta['满分'] || null),
                    current_grade: reviewMode ? (currentGradeValue || null) : null,
                    supports_text: !reviewMode && !!document.querySelector('#submissionLink, textarea[name="studentSubmission.text"]'),
                    supports_file_upload: !reviewMode && !!document.querySelector('input[type="file"], #newFile_table'),
                    supports_library_upload:
                      !reviewMode &&
                      !![...document.querySelectorAll('a, button, input')].find(
                        node => readText(node).indexOf('浏览资源库') !== -1
                      ),
                    supports_comment:
                      !reviewMode &&
                      !!document.querySelector('textarea[name="student_commentstext"], textarea[name*="comment"], textarea[id*="comment"]'),
                    instructions: instructionNodes.slice(0, 12),
                    instructions_html: instructionHtmlNode ? instructionHtmlNode.innerHTML : null,
                    attachments: attachmentEntries,
                    submitted_files: submittedFiles,
                  };
                }
                """
            )

            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise AssignmentScrapeError(f"加载作业详情超时：{exc}") from exc
    except Exception as exc:  # pragma: no cover - operational fallback
        raise AssignmentScrapeError(f"抓取作业详情失败：{exc}") from exc

    page_title = raw["page_title"]
    mode = "review" if page_title.startswith("复查提交历史记录") else "submit"
    instructions = [text for text in raw["instructions"] if text]
    submitted_files = []
    seen = set()
    for label in raw["submitted_files"]:
        if label in seen:
            continue
        seen.add(label)
        submitted_files.append(label)

    return AssignmentDetail(
        item=item,
        page_title=page_title,
        mode=mode,
        due_at=raw["due_at"],
        points_possible=raw["points_possible"],
        current_grade=raw["current_grade"],
        supports_text=raw["supports_text"],
        supports_file_upload=raw["supports_file_upload"],
        supports_library_upload=raw["supports_library_upload"],
        supports_comment=raw["supports_comment"],
        instructions=instructions,
        instructions_html=raw.get("instructions_html"),
        attachments=[AssignmentAsset(label=entry["label"], url=entry["href"]) for entry in raw.get("attachments", [])],
        submitted_files=submitted_files,
    )


def download_assignment(
    *,
    storage_state_path: str,
    item: AssignmentItem,
    output_path: str | None = None,
    headless: bool = True,
    timeout_ms: int = 30000,
    timeout_seconds: int = 60,
) -> AssignmentDownloadResult:
    if item.type == "assignment-file":
        target_path = Path(output_path).expanduser().resolve() if output_path else Path.cwd() / _safe_name(item.title)
        if target_path.exists() and target_path.is_dir():
            target_path = target_path / _safe_name(item.title)
        saved_path = _download_file(
            storage_state_path=storage_state_path,
            url=item.url,
            destination=target_path,
            timeout_seconds=timeout_seconds,
        )
        return AssignmentDownloadResult(
            item=item,
            output_path=str(saved_path),
            summary_path=None,
            downloaded_files=[str(saved_path)],
        )

    detail = scrape_assignment_detail(
        storage_state_path=storage_state_path,
        item=item,
        headless=headless,
        timeout_ms=timeout_ms,
    )

    target_dir = Path(output_path).expanduser().resolve() if output_path else Path.cwd() / _safe_name(item.title)
    if target_dir.suffix:
        target_dir = target_dir.parent / target_dir.stem
    target_dir.mkdir(parents=True, exist_ok=True)

    summary_path = target_dir / "作业说明.md"
    summary_path.write_text(_assignment_summary_markdown(detail), encoding="utf-8")

    downloaded_files = [str(summary_path)]
    attachment_dir = target_dir / "附件"
    for asset in detail.attachments:
        saved_path = _download_file(
            storage_state_path=storage_state_path,
            url=asset.url,
            destination=attachment_dir / _safe_name(asset.label),
            timeout_seconds=timeout_seconds,
        )
        downloaded_files.append(str(saved_path))

    return AssignmentDownloadResult(
        item=item,
        output_path=str(target_dir),
        summary_path=str(summary_path),
        downloaded_files=downloaded_files,
    )


def resolve_assignment(items: list[AssignmentItem], needle: str) -> AssignmentItem | None:
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


def submit_assignment(
    *,
    storage_state_path: str,
    item: AssignmentItem,
    text: str | None = None,
    comment: str | None = None,
    files: list[str] | None = None,
    clear_existing_files: bool = False,
    clear_text: bool = False,
    clear_comment: bool = False,
    action: str = "save",
    headless: bool = True,
    timeout_ms: int = 30000,
) -> AssignmentSubmissionResult:
    if item.type != "blackboard-assignment":
        raise AssignmentScrapeError("当前仅支持提交 Blackboard 站内原生作业。")

    if action not in {"save", "submit"}:
        raise AssignmentScrapeError(f"不支持的作业提交动作：{action}")

    file_list = files or []
    blank_text_html = "<p></p>"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            page.goto(item.url, wait_until="domcontentloaded", timeout=timeout_ms)
            if page.title().startswith("复查提交历史记录"):
                continue_button = page.locator('input[name="bottom_继续"]')
                if continue_button.count():
                    with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms):
                        continue_button.click()
                elif not page.locator("form#uploadAssignmentFormId").count():
                    raise AssignmentScrapeError(
                        "该作业当前处于只读复查状态，未发现重新提交入口，可能已经截止、已评分或不允许再次提交。"
                    )
            page.wait_for_selector("form#uploadAssignmentFormId", state="attached", timeout=timeout_ms)
            page.wait_for_function(
                "() => !!(window.newFile_FilePickerObject && typeof window.newFile_FilePickerObject.submitFormUsingAjax === 'function')",
                timeout=timeout_ms,
            )

            if (text or clear_text) and page.locator("#submissionLink").count():
                page.locator("#submissionLink").click(force=True)
                page.wait_for_timeout(500)

            if text or clear_text:
                page.evaluate(
                    """
                    payload => {
                      const field = document.getElementsByName('studentSubmission.text')[0];
                      if (field) {
                        field.value = payload.value;
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                      }
                      if (window.tinyMCE && typeof window.tinyMCE.get === 'function') {
                        const editor = window.tinyMCE.get('studentSubmission.text');
                        if (editor && typeof editor.setContent === 'function') {
                          editor.setContent(payload.value);
                          if (typeof editor.save === 'function') {
                            editor.save();
                          }
                        }
                      }
                    }
                    """,
                    {"value": text if text is not None else blank_text_html, "clear": clear_text},
                )

            if comment or clear_comment:
                page.evaluate(
                    """
                    payload => {
                      const field = document.getElementsByName('student_commentstext')[0];
                      const hiddenField = document.getElementsByName('student_commentstext_f')[0];
                      if (field) {
                        field.value = payload.value;
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                      }
                      if (hiddenField && payload.clear) {
                        hiddenField.value = '';
                      }
                    }
                    """,
                    {"value": comment or "", "clear": clear_comment},
                )

            if file_list:
                page.locator("#newFile_chooseLocalFile").set_input_files(file_list)

            if clear_existing_files and page.locator('input[name="newFile_remove"]').count():
                page.evaluate(
                    """
                    () => {
                      const markers = document.querySelectorAll('input[name="newFile_remove"]');
                      for (let i = 0; i < markers.length; i += 1) {
                        markers[i].disabled = false;
                      }
                    }
                    """
                )

            page.evaluate(
                """
                () => {
                  if (typeof window.finalizeEditors === 'function') {
                    window.finalizeEditors();
                  }
                }
                """
            )

            button_name = "bottom_保存草稿" if action == "save" else "bottom_提交"
            response_status: int | None = None
            button_selector = f'input[name="{button_name}"]'

            try:
                with page.expect_response(
                    lambda response: "/webapps/assignment/uploadAssignment?action=submit" in response.url
                    and response.request.method.upper() == "POST",
                    timeout=5000,
                ) as response_info:
                    page.locator(button_selector).click(force=True)
                response_status = response_info.value.status
            except PlaywrightTimeoutError:
                if page.locator("#agree_button").count():
                    page.locator("#agree_button").click()
                    page.wait_for_timeout(1000)
                    with page.expect_response(
                        lambda response: "/webapps/assignment/uploadAssignment?action=submit" in response.url
                        and response.request.method.upper() == "POST",
                        timeout=timeout_ms,
                    ) as response_info:
                        page.locator(button_selector).click(force=True)
                    response_status = response_info.value.status
                else:
                    raise

            page.wait_for_timeout(3000)

            page_title = page.title()
            page_text_excerpt = page.locator("body").inner_text(timeout=timeout_ms)[:1200]
            final_url = page.url

            ok = response_status < 400 and "失败" not in page_text_excerpt and "重试" not in page_text_excerpt
            note = None
            if action == "save":
                note = "已对真实教学网站执行草稿保存尝试。"
            elif action == "submit":
                note = "已对真实教学网站执行最终提交尝试。"

            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise AssignmentScrapeError(f"提交作业时超时：{exc}") from exc
    except Exception as exc:  # pragma: no cover - operational fallback
        raise AssignmentScrapeError(f"提交作业失败：{exc}") from exc

    return AssignmentSubmissionResult(
        item=item,
        action=action,
        final_url=final_url,
        page_title=page_title,
        page_text_excerpt=page_text_excerpt,
        response_status=response_status,
        ok=ok,
        note=note,
    )


def _find_assignment_menu(info: CourseInfo) -> dict[str, str | None] | None:
    for item in info.menu_items:
        label = (item.get("label") or "").strip()
        if "作业" in label:
            return item
    return None


def _infer_assignment_type(url: str) -> str:
    parsed = urlparse(url)
    if "/webapps/assignment/uploadAssignment" in parsed.path:
        return "blackboard-assignment"
    if "/bbcswebdav/" in parsed.path:
        return "assignment-file"
    if parsed.netloc and parsed.netloc != "course.pku.edu.cn":
        return "external-assignment"
    return "assignment-link"


def _parse_identifier(url: str) -> str | None:
    for part in ("content_id=", "course_id=", "rid-", "xid-"):
        if part in url:
            tail = url.split(part, 1)[1]
            for sep in ("&", "/", "#"):
                if sep in tail:
                    tail = tail.split(sep, 1)[0]
            return tail
    return None


def _assignment_summary_markdown(detail: AssignmentDetail) -> str:
    lines = [
        f"# {detail.item.title}",
        "",
        f"- 作业 ID：{detail.item.id or '无'}",
        f"- 类型：{detail.item.type}",
        f"- 页面模式：{detail.mode}",
        f"- 截止时间：{detail.due_at or '未知'}",
        f"- 分值：{detail.points_possible or '未知'}",
        f"- 当前成绩：{detail.current_grade or '暂无'}",
        f"- 支持文本提交：{'是' if detail.supports_text else '否'}",
        f"- 支持文件上传：{'是' if detail.supports_file_upload else '否'}",
        f"- 支持资源库上传：{'是' if detail.supports_library_upload else '否'}",
        f"- 支持备注：{'是' if detail.supports_comment else '否'}",
        "",
        "## 作业说明",
        "",
    ]
    if detail.instructions:
        for text in detail.instructions:
            lines.append(f"- {text}")
    else:
        lines.append("暂无可解析的作业说明。")

    lines.extend(["", "## 附件", ""])
    if detail.attachments:
        for asset in detail.attachments:
            lines.append(f"- {asset.label}")
            lines.append(f"  - {asset.url}")
    else:
        lines.append("暂无附件。")

    return "\n".join(lines) + "\n"


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
