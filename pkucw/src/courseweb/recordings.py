from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .courses import CourseInfo, CourseRecord, CourseScrapeError, scrape_course_info


class RecordingScrapeError(RuntimeError):
    """Raised when classroom recording scraping or download cannot complete."""


INVALID_PATH_CHARS_RE = re.compile(r'[\\/:*?"<>|]+')
EXTINF_RE = re.compile(r"#EXTINF:([0-9.]+)")
KEY_URI_RE = re.compile(r'URI="([^"]+)"')
KEY_IV_RE = re.compile(r"IV=0x([0-9A-Fa-f]+)")


@dataclass(slots=True)
class RecordingItem:
    id: str | None
    title: str
    recorded_at: str | None
    teacher: str | None
    watch_url: str
    course_id: str | None
    player_course_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "recorded_at": self.recorded_at,
            "teacher": self.teacher,
            "watch_url": self.watch_url,
            "course_id": self.course_id,
            "player_course_id": self.player_course_id,
        }


@dataclass(slots=True)
class RecordingDetail:
    item: RecordingItem
    page_title: str | None
    final_url: str
    player_url: str | None
    playlist_url: str | None
    encrypted: bool
    segment_count: int
    duration_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording": self.item.to_dict(),
            "page_title": self.page_title,
            "final_url": self.final_url,
            "player_url": self.player_url,
            "playlist_url": self.playlist_url,
            "encrypted": self.encrypted,
            "segment_count": self.segment_count,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(slots=True)
class MediaArtifact:
    path: str
    exists: bool
    size_bytes: int | None
    sha256: str | None
    duration_seconds: float | None
    is_playable: bool | None
    has_video: bool | None
    has_audio: bool | None
    probe_tool: str | None
    probe_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "duration_seconds": self.duration_seconds,
            "is_playable": self.is_playable,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "probe_tool": self.probe_tool,
            "probe_error": self.probe_error,
        }


@dataclass(slots=True)
class RecordingDownloadResult:
    item: RecordingItem
    output_path: str
    ts_output_path: str
    mp4_output_path: str | None
    playlist_url: str
    segment_count: int
    duration_seconds: float | None
    encrypted: bool
    container: str
    remuxed: bool
    remux_tool: str | None
    note: str | None
    ts_artifact: MediaArtifact
    mp4_artifact: MediaArtifact | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording": self.item.to_dict(),
            "output_path": self.output_path,
            "ts_output_path": self.ts_output_path,
            "mp4_output_path": self.mp4_output_path,
            "playlist_url": self.playlist_url,
            "segment_count": self.segment_count,
            "duration_seconds": self.duration_seconds,
            "encrypted": self.encrypted,
            "container": self.container,
            "remuxed": self.remuxed,
            "remux_tool": self.remux_tool,
            "note": self.note,
            "ts_artifact": self.ts_artifact.to_dict(),
            "mp4_artifact": self.mp4_artifact.to_dict() if self.mp4_artifact else None,
        }


@dataclass(slots=True)
class HlsMediaPlaylist:
    playlist_url: str
    key_url: str | None
    iv_hex: str | None
    media_sequence: int
    segment_urls: list[str]
    duration_seconds: float | None
    encrypted: bool


def scrape_recordings(
    *,
    storage_state_path: str,
    course: CourseRecord,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> tuple[CourseInfo, list[RecordingItem]]:
    try:
        info = scrape_course_info(
            storage_state_path=storage_state_path,
            course=course,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    except CourseScrapeError as exc:
        raise RecordingScrapeError(str(exc)) from exc

    menu_item = _find_recordings_menu(info)
    if menu_item is None or not menu_item.get("url"):
        return info, []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            page.goto(menu_item["url"], wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("body", state="attached", timeout=timeout_ms)
            page_title = page.title()
            current_page_url = page.url
            raw_items = page.evaluate(
                """
                () => {
                  const readText = (node) => (node?.textContent || '').replace(/\\s+/g, ' ').trim();
                  const parseRowText = (value) => {
                    const match = value.match(/^(.*?)\\s+时间:\\s*(.*?)\\s+教师:\\s*(.*?)\\s+操作:/);
                    if (!match) return null;
                    return {
                      title: match[1].trim(),
                      recorded_at: match[2].trim(),
                      teacher: match[3].trim(),
                    };
                  };

                  const rows = [...document.querySelectorAll('table tr, tbody tr')];
                  return rows
                    .map((row) => {
                      const link = row.querySelector('a[href*="playVideo.action"]');
                      if (!link) return null;
                      const titleCell = readText(row.querySelector('th[scope="row"], th'));
                      const dataCells = [...row.querySelectorAll('td .table-data-cell-value')]
                        .map((cell) => readText(cell))
                        .filter(Boolean);
                      const rowText = readText(row);
                      const parsed = parseRowText(rowText) || {};
                      return {
                        title: titleCell || parsed.title || readText(link),
                        recorded_at: dataCells[0] || parsed.recorded_at || null,
                        teacher: dataCells[1] || parsed.teacher || null,
                        watch_url: new URL(link.getAttribute('href') || '', window.location.href).href,
                      };
                    })
                    .filter((item) => item && item.title && item.watch_url);
                }
                """
            )
            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise RecordingScrapeError(f"加载课堂实录列表超时：{exc}") from exc
    except Exception as exc:
        raise RecordingScrapeError(f"抓取课堂实录失败：{exc}") from exc

    recording_info = CourseInfo(
        course=info.course,
        page_title=page_title,
        current_page_url=current_page_url,
        current_page_label="课堂实录",
        menu_items=info.menu_items,
    )
    return recording_info, [_normalize_recording(raw, course.id) for raw in raw_items]


def resolve_recording(items: list[RecordingItem], needle: str) -> RecordingItem | None:
    raw = needle.strip().lower()
    if not raw:
        return None

    for item in items:
        if item.id and raw == item.id.lower():
            return item

    for item in items:
        if raw in item.title.lower():
            return item

    for item in items:
        if item.recorded_at and raw in item.recorded_at.lower():
            return item

    return None


def scrape_recording_detail(
    *,
    storage_state_path: str,
    item: RecordingItem,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> RecordingDetail:
    detail, _ = _capture_recording_detail(
        storage_state_path=storage_state_path,
        item=item,
        headless=headless,
        timeout_ms=timeout_ms,
        include_state=False,
    )
    return detail


def download_recording(
    *,
    storage_state_path: str,
    item: RecordingItem,
    output_path: str | None = None,
    headless: bool = True,
    timeout_ms: int = 30000,
    timeout_seconds: int = 60,
    remux_to_mp4: bool = True,
    show_progress: bool = True,
) -> RecordingDownloadResult:
    detail, playback_state = _capture_recording_detail(
        storage_state_path=storage_state_path,
        item=item,
        headless=headless,
        timeout_ms=timeout_ms,
        include_state=True,
    )

    if detail.playlist_url is None or playback_state is None:
        raise RecordingScrapeError("无法从播放页面解析课堂实录的流地址。")

    playlist = _load_hls_playlist(detail.playlist_url)
    if not playlist.segment_urls:
        raise RecordingScrapeError("课堂实录播放列表中没有可下载的媒体分片。")

    target_path, preferred_mp4_path = _resolve_output_paths(item=item, output_path=output_path)
    headers = {
        "User-Agent": "courseweb-cli/0.1",
        "Referer": detail.player_url or "https://onlineroomse.pku.edu.cn/",
        "Origin": "https://onlineroomse.pku.edu.cn",
    }

    key_bytes = None
    if playlist.key_url:
        key_bytes = _request_bytes(
            playlist.key_url,
            state=playback_state,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_state = _init_download_checkpoint(total=len(playlist.segment_urls))
    with target_path.open("wb") as handle:
        for index, segment_url in enumerate(playlist.segment_urls):
            payload = _request_bytes(
                segment_url,
                state=playback_state,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
            if key_bytes is not None:
                payload = _decrypt_aes_segment(
                    payload,
                    key=key_bytes,
                    iv=_segment_iv(playlist, index),
                )
            handle.write(payload)
            if show_progress:
                _emit_download_progress(
                    item=item,
                    index=index + 1,
                    total=len(playlist.segment_urls),
                )
            else:
                _emit_download_checkpoint(
                    item=item,
                    index=index + 1,
                    total=len(playlist.segment_urls),
                    state=checkpoint_state,
                )

    if show_progress:
        print(file=sys.stderr)

    mp4_output_path = None
    final_output_path = target_path
    final_container = "mpeg-ts"
    remuxed = False
    remux_tool = None
    note = None
    if remux_to_mp4:
        remux_tool = _detect_remux_tool()
        if remux_tool is None:
            note = "本机没有可用的转封装工具，因此保留为 .ts 文件。"
        else:
            try:
                _remux_ts_to_mp4(
                    input_path=target_path,
                    output_path=preferred_mp4_path,
                    tool=remux_tool,
                )
                mp4_output_path = str(preferred_mp4_path)
                final_output_path = preferred_mp4_path
                final_container = "mp4"
                remuxed = True
            except RecordingScrapeError as exc:
                note = str(exc)

    if show_progress:
        print("正在校验已下载的媒体文件...", file=sys.stderr, flush=True)

    ts_artifact = _inspect_media_artifact(target_path)
    mp4_artifact = _inspect_media_artifact(final_output_path) if remuxed else None

    return RecordingDownloadResult(
        item=item,
        output_path=str(final_output_path),
        ts_output_path=str(target_path),
        mp4_output_path=mp4_output_path,
        playlist_url=playlist.playlist_url,
        segment_count=len(playlist.segment_urls),
        duration_seconds=playlist.duration_seconds,
        encrypted=playlist.encrypted,
        container=final_container,
        remuxed=remuxed,
        remux_tool=remux_tool,
        note=note,
        ts_artifact=ts_artifact,
        mp4_artifact=mp4_artifact,
    )


def _capture_recording_detail(
    *,
    storage_state_path: str,
    item: RecordingItem,
    headless: bool,
    timeout_ms: int,
    include_state: bool,
) -> tuple[RecordingDetail, dict[str, Any] | None]:
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state_path, ignore_https_errors=True)
            page = context.new_page()
            request_urls: list[str] = []
            page.on("request", lambda request: request_urls.append(request.url))
            page.goto(item.watch_url, wait_until="domcontentloaded", timeout=timeout_ms)
            _wait_for_stream_requests(page=page, request_urls=request_urls, timeout_ms=timeout_ms)

            player_url = next((frame.url for frame in page.frames if "/player?" in frame.url), None)
            playlist_url = next((url for url in reversed(request_urls) if ".m3u8" in url.lower()), None)
            page_title = page.title()
            playlist = _load_hls_playlist(playlist_url) if playlist_url else None
            detail = RecordingDetail(
                item=item,
                page_title=page_title,
                final_url=page.url,
                player_url=player_url,
                playlist_url=playlist.playlist_url if playlist else playlist_url,
                encrypted=playlist.encrypted if playlist else False,
                segment_count=len(playlist.segment_urls) if playlist else 0,
                duration_seconds=playlist.duration_seconds if playlist else None,
            )
            state = context.storage_state() if include_state else None
            context.close()
            browser.close()
            return detail, state
    except PlaywrightTimeoutError as exc:
        raise RecordingScrapeError(f"打开课堂实录播放器超时：{exc}") from exc
    except Exception as exc:
        raise RecordingScrapeError(f"打开课堂实录播放器失败：{exc}") from exc


def _wait_for_stream_requests(*, page, request_urls: list[str], timeout_ms: int) -> None:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if any(".m3u8" in url.lower() for url in request_urls):
            page.wait_for_timeout(1000)
            return
        page.wait_for_timeout(500)


def _load_hls_playlist(url: str) -> HlsMediaPlaylist:
    playlist_url = url
    text = _request_text(url, headers={"User-Agent": "courseweb-cli/0.1"})

    if "#EXT-X-STREAM-INF" in text:
        variants = _parse_variant_playlist(text, base_url=url)
        if not variants:
            raise RecordingScrapeError("课堂实录返回了主播放列表，但没有可用的媒体清晰度分支。")
        playlist_url = variants[0]
        text = _request_text(playlist_url, headers={"User-Agent": "courseweb-cli/0.1"})

    media_sequence = 0
    durations: list[float] = []
    segment_urls: list[str] = []
    key_url = None
    iv_hex = None

    for line in text.splitlines():
        current = line.strip()
        if not current:
            continue
        if current.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                media_sequence = int(current.split(":", 1)[1])
            except ValueError:
                media_sequence = 0
            continue
        if current.startswith("#EXT-X-KEY:"):
            match = KEY_URI_RE.search(current)
            key_url = urljoin(playlist_url, match.group(1)) if match else None
            iv_match = KEY_IV_RE.search(current)
            iv_hex = iv_match.group(1) if iv_match else None
            continue
        if current.startswith("#EXTINF:"):
            match = EXTINF_RE.match(current)
            if match:
                durations.append(float(match.group(1)))
            continue
        if current.startswith("#"):
            continue
        segment_urls.append(urljoin(playlist_url, current))

    return HlsMediaPlaylist(
        playlist_url=playlist_url,
        key_url=key_url,
        iv_hex=iv_hex,
        media_sequence=media_sequence,
        segment_urls=segment_urls,
        duration_seconds=round(sum(durations), 3) if durations else None,
        encrypted=key_url is not None,
    )


def _parse_variant_playlist(text: str, *, base_url: str) -> list[str]:
    variants: list[tuple[int, str]] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        bandwidth = 0
        for part in line.split(":", 1)[1].split(","):
            if part.startswith("BANDWIDTH="):
                try:
                    bandwidth = int(part.split("=", 1)[1])
                except ValueError:
                    bandwidth = 0
        next_index = index + 1
        while next_index < len(lines) and lines[next_index].startswith("#"):
            next_index += 1
        if next_index < len(lines):
            variants.append((bandwidth, urljoin(base_url, lines[next_index])))
    variants.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in variants]


def _request_text(url: str, *, headers: dict[str, str]) -> str:
    request = Request(url, headers=headers)
    ssl_context = ssl._create_unverified_context()
    with urlopen(request, timeout=60, context=ssl_context) as response:
        return response.read().decode("utf-8", errors="replace")


def _request_bytes(
    url: str,
    *,
    state: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
) -> bytes:
    request_headers = dict(headers)
    cookie_header = _cookie_header_for_state(state, url)
    if cookie_header:
        request_headers["Cookie"] = cookie_header
    request = Request(url, headers=request_headers)
    ssl_context = ssl._create_unverified_context()
    with urlopen(request, timeout=timeout_seconds, context=ssl_context) as response:
        return response.read()


def _cookie_header_for_state(state: dict[str, Any], url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    cookies: list[str] = []
    for cookie in state.get("cookies", []):
        domain = str(cookie.get("domain") or "")
        if not host.endswith(domain.lstrip(".")):
            continue
        cookie_path = str(cookie.get("path") or "/")
        if not path.startswith(cookie_path):
            continue
        cookies.append(f"{cookie['name']}={cookie['value']}")
    return "; ".join(cookies)


def _decrypt_aes_segment(payload: bytes, *, key: bytes, iv: bytes) -> bytes:
    command = [
        "/usr/bin/openssl",
        "enc",
        "-d",
        "-aes-128-cbc",
        "-K",
        key.hex(),
        "-iv",
        iv.hex(),
    ]
    result = subprocess.run(
        command,
        input=payload,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        error_text = result.stderr.decode("utf-8", errors="replace").strip()
        raise RecordingScrapeError(f"无法解密 HLS 分片：{error_text or 'openssl 执行失败'}")
    return result.stdout


def _segment_iv(playlist: HlsMediaPlaylist, index: int) -> bytes:
    if playlist.iv_hex:
        return bytes.fromhex(playlist.iv_hex)
    sequence = playlist.media_sequence + index
    return sequence.to_bytes(16, byteorder="big")


def _resolve_output_paths(*, item: RecordingItem, output_path: str | None) -> tuple[Path, Path]:
    if output_path:
        target = Path(output_path).expanduser().resolve()
    else:
        target = Path.cwd() / _safe_name(item.title)

    if target.exists() and target.is_dir():
        target = target / _safe_name(item.title)

    suffix = target.suffix.lower()
    if suffix == ".mp4":
        return target.with_suffix(".ts"), target
    if suffix == ".ts":
        return target, target.with_suffix(".mp4")
    if suffix == "":
        return target.with_name(target.name + ".ts"), target.with_name(target.name + ".mp4")
    return target, target.with_suffix(".mp4")


def _emit_download_progress(*, item: RecordingItem, index: int, total: int) -> None:
    percent = (index / total) * 100 if total else 100
    print(
        f"\r正在下载 {item.title}：{index}/{total} 个分片（{percent:5.1f}%）",
        end="",
        file=sys.stderr,
        flush=True,
    )


def _init_download_checkpoint(total: int) -> dict[str, float | int]:
    now = time.monotonic()
    return {
        "started_at": now,
        "last_emit_at": now,
        "last_emit_index": 0,
        "total": total,
    }


def _emit_download_checkpoint(
    *,
    item: RecordingItem,
    index: int,
    total: int,
    state: dict[str, float | int],
) -> None:
    now = time.monotonic()
    last_emit_at = float(state.get("last_emit_at", now))
    last_emit_index = int(state.get("last_emit_index", 0))
    should_emit = False

    # Keep long-running agent workflows alive without spamming logs.
    if index >= total:
        should_emit = True
    elif index == 1:
        should_emit = True
    elif index - last_emit_index >= 100:
        should_emit = True
    elif now - last_emit_at >= 20:
        should_emit = True

    if not should_emit:
        return

    started_at = float(state.get("started_at", now))
    elapsed = max(now - started_at, 0.0)
    percent = (index / total) * 100 if total else 100.0
    print(
        (
            f"课堂回放下载中：{item.title} "
            f"{index}/{total} 个分片（{percent:5.1f}%），"
            f"已运行 {elapsed:0.1f}s"
        ),
        file=sys.stderr,
        flush=True,
    )
    state["last_emit_at"] = now
    state["last_emit_index"] = index


def _detect_remux_tool() -> str | None:
    if shutil.which("swift"):
        return "swift-avfoundation"
    return None


def _detect_probe_tool() -> str | None:
    if shutil.which("swift"):
        return "swift-avfoundation"
    return None


def _remux_ts_to_mp4(*, input_path: Path, output_path: Path, tool: str) -> None:
    if tool != "swift-avfoundation":
        raise RecordingScrapeError(f"不支持的转封装工具：{tool}")

    swift_script = """
import Foundation
import AVFoundation

let args = CommandLine.arguments
guard args.count == 3 else {
    fputs("usage: remux input.ts output.mp4\\n", stderr)
    exit(2)
}

let inputURL = URL(fileURLWithPath: args[1])
let outputURL = URL(fileURLWithPath: args[2])
try? FileManager.default.removeItem(at: outputURL)

let semaphore = DispatchSemaphore(value: 0)
let asset = AVURLAsset(url: inputURL)

Task {
    do {
        guard let export = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetPassthrough) else {
            fputs("failed to create export session\\n", stderr)
            exit(3)
        }
        try await export.export(to: outputURL, as: .mp4)
        semaphore.signal()
    } catch {
        fputs("error: \\(error)\\n", stderr)
        exit(4)
    }
}

_ = semaphore.wait(timeout: .now() + 3600)
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".swift", delete=False) as handle:
        handle.write(swift_script)
        script_path = Path(handle.name)

    try:
        result = subprocess.run(
            ["/usr/bin/swift", str(script_path), str(input_path), str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0 or not output_path.exists():
        error_text = (result.stderr or result.stdout).strip()
        raise RecordingScrapeError(
            f"已成功下载 .ts 文件，但无法转封装为 .mp4：{error_text or 'swift 导出失败'}"
        )


def _inspect_media_artifact(path: Path) -> MediaArtifact:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return MediaArtifact(
            path=str(resolved),
            exists=False,
            size_bytes=None,
            sha256=None,
            duration_seconds=None,
            is_playable=None,
            has_video=None,
            has_audio=None,
            probe_tool=None,
            probe_error=None,
        )

    probe_tool = _detect_probe_tool()
    duration_seconds = None
    is_playable = None
    has_video = None
    has_audio = None
    probe_error = None
    if probe_tool is not None:
        try:
            probe = _probe_media_file(path=resolved, tool=probe_tool)
            duration_seconds = probe.get("duration_seconds")
            is_playable = probe.get("is_playable")
            has_video = probe.get("has_video")
            has_audio = probe.get("has_audio")
        except RecordingScrapeError as exc:
            probe_error = str(exc)

    return MediaArtifact(
        path=str(resolved),
        exists=True,
        size_bytes=resolved.stat().st_size,
        sha256=_sha256_for_file(resolved),
        duration_seconds=duration_seconds,
        is_playable=is_playable,
        has_video=has_video,
        has_audio=has_audio,
        probe_tool=probe_tool,
        probe_error=probe_error,
    )


def _probe_media_file(*, path: Path, tool: str) -> dict[str, Any]:
    if tool != "swift-avfoundation":
        raise RecordingScrapeError(f"不支持的媒体探测工具：{tool}")

    swift_script = """
import Foundation
import AVFoundation

let args = CommandLine.arguments
guard args.count == 2 else {
    fputs("usage: probe input\\n", stderr)
    exit(2)
}

let inputURL = URL(fileURLWithPath: args[1])
let asset = AVURLAsset(url: inputURL)
let durationSeconds = CMTimeGetSeconds(asset.duration)
let tracks = asset.tracks
let hasVideo = tracks.contains { $0.mediaType == .video }
let hasAudio = tracks.contains { $0.mediaType == .audio }
let payload: [String: Any] = [
    "duration_seconds": durationSeconds.isFinite ? durationSeconds : NSNull(),
    "is_playable": asset.isPlayable,
    "has_video": hasVideo,
    "has_audio": hasAudio
]
let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
print(String(decoding: data, as: UTF8.self))
"""

    with tempfile.NamedTemporaryFile("w", suffix=".swift", delete=False) as handle:
        handle.write(swift_script)
        script_path = Path(handle.name)

    try:
        result = subprocess.run(
            ["/usr/bin/swift", str(script_path), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0:
        error_text = (result.stderr or result.stdout).strip()
        raise RecordingScrapeError(f"无法探测媒体文件 {path.name}：{error_text or 'swift 探测失败'}")
    return json.loads(result.stdout or "{}")


def _sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_recording(raw: dict[str, str | None], course_id: str | None) -> RecordingItem:
    metadata = _decode_watch_token(raw["watch_url"] or "")
    return RecordingItem(
        id=metadata.get("hqySubId") or _parse_token(raw["watch_url"] or ""),
        title=(raw.get("title") or "").strip(),
        recorded_at=(raw.get("recorded_at") or metadata.get("recordTime") or None),
        teacher=(raw.get("teacher") or None),
        watch_url=raw["watch_url"] or "",
        course_id=course_id,
        player_course_id=metadata.get("hqyCourseId"),
    )


def _decode_watch_token(url: str) -> dict[str, str]:
    token = _parse_token(url)
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return {str(key): str(value) for key, value in data.items() if value is not None}


def _parse_token(url: str) -> str | None:
    parsed = urlparse(url)
    values = parse_qs(parsed.query)
    token = values.get("token", [None])[0]
    return token


def _find_recordings_menu(info: CourseInfo) -> dict[str, str | None] | None:
    for item in info.menu_items:
        label = (item.get("label") or "").strip()
        if item.get("kind") == "recordings" or "实录" in label or "回放" in label:
            return item
    return None


def _safe_name(value: str) -> str:
    cleaned = INVALID_PATH_CHARS_RE.sub("_", value).strip().rstrip(".")
    return cleaned or "recording"
