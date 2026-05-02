import asyncio
import os
from typing import Dict, List, Optional, Tuple

import requests
from vercel.blob import AsyncBlobClient, list_objects


ARCHIVE_PREFIX = "generated-newsletters"


def blob_archive_configured() -> bool:
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN"))


def _is_public_store_access_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "private access on a public store" in message


def _normalize_items(blob_page) -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    for blob in getattr(blob_page, "blobs", []):
        pathname = getattr(blob, "pathname", "")
        if not pathname.endswith(".html"):
            continue
        items.append(
            {
                "pathname": pathname,
                "filename": pathname.split("/")[-1],
                "size": getattr(blob, "size", 0),
                "uploaded_at": getattr(blob, "uploaded_at", None),
                "content_type": getattr(blob, "content_type", "text/html"),
                "url": getattr(blob, "url", ""),
            }
        )
    return items


def list_archived_newsletters(limit: int = 100) -> List[Dict[str, object]]:
    if not blob_archive_configured():
        return []

    cursor = None
    items: List[Dict[str, object]] = []

    while True:
        page = list_objects(prefix=ARCHIVE_PREFIX + "/", limit=limit, cursor=cursor)
        items.extend(_normalize_items(page))
        cursor = getattr(page, "cursor", None)
        if not getattr(page, "has_more", False) or not cursor:
            break

    items.sort(key=lambda item: item.get("uploaded_at") or 0, reverse=True)
    return items


async def _put_html_with_access(pathname: str, html_content: str, access: str):
    client = AsyncBlobClient()
    return await client.put(
        pathname,
        html_content.encode("utf-8"),
        access=access,
        add_random_suffix=False,
        content_type="text/html; charset=utf-8",
    )


async def _put_html(pathname: str, html_content: str):
    try:
        return await _put_html_with_access(pathname, html_content, "private")
    except Exception as exc:
        if not _is_public_store_access_error(exc):
            raise
    return await _put_html_with_access(pathname, html_content, "public")


def save_newsletter_html(filename: str, html_content: str) -> Dict[str, object]:
    if not blob_archive_configured():
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured")

    pathname = f"{ARCHIVE_PREFIX}/{filename}"
    result = asyncio.run(_put_html(pathname, html_content))
    return {
        "pathname": result.pathname,
        "url": getattr(result, "url", ""),
        "download_url": getattr(result, "download_url", ""),
        "content_type": getattr(result, "content_type", "text/html; charset=utf-8"),
        "etag": getattr(result, "etag", ""),
    }


async def _read_html_with_access(pathname: str, access: str):
    client = AsyncBlobClient()
    result = await client.get(pathname, access=access)
    if result is None:
        return None

    status_code = getattr(result, "status_code", 200)
    if status_code != 200:
        return None

    raw_bytes = b""
    stream = getattr(result, "stream", None)
    if stream is not None:
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        raw_bytes = b"".join(chunks)
    else:
        body = getattr(result, "body", None)
        if isinstance(body, bytes):
            raw_bytes = body
        elif isinstance(body, str):
            raw_bytes = body.encode("utf-8")
        elif isinstance(result, (bytes, bytearray)):
            raw_bytes = bytes(result)
        else:
            text = getattr(result, "text", None)
            if isinstance(text, str):
                raw_bytes = text.encode("utf-8")
            else:
                return None

    blob_meta = getattr(result, "blob", None)
    content_type = (
        getattr(blob_meta, "content_type", None)
        or getattr(result, "content_type", None)
        or "text/html; charset=utf-8"
    )

    return raw_bytes, content_type


async def _read_html(pathname: str):
    try:
        data = await _read_html_with_access(pathname, "private")
        if data is not None:
            return data
    except Exception as exc:
        if not _is_public_store_access_error(exc):
            raise
    return await _read_html_with_access(pathname, "public")


def _read_html_via_blob_url(pathname: str) -> Optional[Tuple[bytes, str]]:
    for item in list_archived_newsletters(limit=200):
        if item.get("pathname") != pathname:
            continue
        blob_url = item.get("url")
        if not blob_url:
            continue
        try:
            response = requests.get(blob_url, timeout=20)
            if response.status_code != 200:
                continue
            return (
                response.content,
                response.headers.get("content-type", "text/html; charset=utf-8"),
            )
        except Exception:
            continue
    return None


def load_newsletter_html(pathname: str) -> Optional[Tuple[bytes, str]]:
    if not blob_archive_configured():
        return None
    data = asyncio.run(_read_html(pathname))
    if data is not None:
        return data
    return _read_html_via_blob_url(pathname)


def is_valid_archive_path(pathname: str) -> bool:
    return pathname.startswith(ARCHIVE_PREFIX + "/") and pathname.endswith(".html")
