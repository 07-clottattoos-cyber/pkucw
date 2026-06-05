from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


FILE_NAME_RE = re.compile(r'filename\\*?=(?:UTF-8\'\')?"?([^";]+)"?')
INVALID_PATH_CHARS_RE = re.compile(r'[\\\\/:*?"<>|]+')

CONTENT_TYPE_SUFFIXES = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/msword": ".doc",
    "application/vnd.ms-word": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "text/plain": ".txt",
    "text/html": ".html",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
}

MAGIC_SIGNATURES = (
    (b"%PDF-", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"PK\x03\x04", ".zip"),
    (b"ftyp", ".mp4"),
)


def safe_download_name(value: str) -> str:
    cleaned = INVALID_PATH_CHARS_RE.sub("_", value).strip().rstrip(".")
    return cleaned or "download"


def filename_from_content_disposition(header: str) -> str | None:
    if not header:
        return None
    match = FILE_NAME_RE.search(header)
    if not match:
        return None
    name = match.group(1).strip()
    return unquote(name.replace("%20", " "))


def resolve_download_destination(
    *,
    destination: Path,
    url: str,
    content_disposition: str,
    content_type: str,
    payload: bytes,
) -> Path:
    if destination.suffix:
        return destination

    inferred_name = filename_from_content_disposition(content_disposition)
    if inferred_name:
        return destination.with_name(safe_download_name(inferred_name))

    suffix = _infer_suffix(url=url, content_type=content_type, payload=payload)
    if suffix:
        return destination.with_name(destination.name + suffix)
    return destination


def _infer_suffix(*, url: str, content_type: str, payload: bytes) -> str | None:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in CONTENT_TYPE_SUFFIXES:
        return CONTENT_TYPE_SUFFIXES[normalized]

    guessed = mimetypes.guess_extension(normalized) if normalized else None
    if guessed:
        return guessed

    path_suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if path_suffix:
        return path_suffix

    for signature, suffix in MAGIC_SIGNATURES:
        if signature == b"ftyp":
            if len(payload) >= 8 and payload[4:8] == signature:
                return suffix
            continue
        if payload.startswith(signature):
            return suffix

    if payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if normalized in {"application/vnd.ms-powerpoint", "application/mspowerpoint"}:
            return ".ppt"
        if normalized in {"application/vnd.ms-excel"}:
            return ".xls"
        if normalized in {"application/msword", "application/vnd.ms-word"}:
            return ".doc"

    return None
