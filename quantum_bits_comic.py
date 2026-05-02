import html
import mimetypes
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup


RSS_FEED_URL = "https://quantumbitscomics.com/feed/"
WP_POSTS_API_URL = "https://quantumbitscomics.com/wp-json/wp/v2/posts"
SERIES_TITLE = "Quantum Bits with Quantessa & Atomique"
CREATOR_NAME = "Yuval Boger"
REQUEST_TIMEOUT = 20
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def fetch_latest_quantum_bits_comic(run_dir: str, session: Optional[requests.Session] = None) -> Optional[Dict[str, Any]]:
    client = session or requests.Session()

    try:
        try:
            feed_response = client.get(RSS_FEED_URL, timeout=REQUEST_TIMEOUT)
            feed_response.raise_for_status()
            try:
                comic = _parse_latest_feed_item(feed_response.text)
            except ET.ParseError:
                comic = None
        except requests.RequestException as exc:
            print(f"Warning: could not fetch Quantum Bits comic: {exc}")
            return None

        if comic is None:
            try:
                comic = _fetch_latest_from_wordpress(client)
            except requests.RequestException as exc:
                print(f"Warning: could not fetch Quantum Bits comic fallback data: {exc}")
                return None

        if comic is not None and _comic_needs_enrichment(comic):
            try:
                enriched = _fetch_post_from_wordpress(comic.get("link"), client)
                if enriched is not None:
                    comic = _merge_comic_payloads(comic, enriched)
            except requests.RequestException as exc:
                print(f"Warning: could not enrich Quantum Bits comic from WordPress: {exc}")

        if comic is None or not comic.get("image_url"):
            return comic

        try:
            image_filename, local_path = _download_image(client, comic["image_url"], run_dir)
            comic["image_filename"] = image_filename
            comic["local_path"] = os.path.relpath(local_path, PROJECT_ROOT)
        except (OSError, requests.RequestException) as exc:
            print(f"Warning: could not cache Quantum Bits comic image locally: {exc}")

        return comic
    except Exception as exc:
        print(f"Warning: unexpected error fetching Quantum Bits comic: {exc}")
        return None


def build_comic_asset_url(run_id: str, image_filename: Optional[str]) -> Optional[str]:
    if not run_id or not image_filename:
        return None
    return f"/runs/{quote(run_id, safe='')}/assets/{quote(image_filename, safe='')}"


def resolve_comic_for_render(comic: Optional[Dict[str, Any]], image_src: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not comic:
        return None

    rendered = dict(comic)
    rendered["image_src"] = image_src or comic.get("local_path") or comic.get("image_url")
    return rendered


def _parse_latest_feed_item(feed_xml: str) -> Optional[Dict[str, Any]]:
    root = ET.fromstring(feed_xml)
    channel = root.find("channel")
    if channel is None:
        return None

    items = channel.findall("item")
    if not items:
        return None

    latest_item = max(items, key=_item_sort_key)
    description_html = _child_text(latest_item, "description")

    return {
        "series": SERIES_TITLE,
        "creator": CREATOR_NAME,
        "title": html.unescape(_child_text(latest_item, "title")),
        "link": _child_text(latest_item, "link"),
        "published_at": _normalize_pubdate(_child_text(latest_item, "pubDate")),
        "published_label": _format_pubdate(_child_text(latest_item, "pubDate")),
        "summary": _extract_summary(description_html),
        "image_url": _extract_image_url(description_html),
        "feed_url": RSS_FEED_URL,
        "source": "rss"
    }


def _fetch_latest_from_wordpress(session: requests.Session) -> Optional[Dict[str, Any]]:
    response = session.get(f"{WP_POSTS_API_URL}?per_page=1&_embed=1", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not payload:
        return None
    return _comic_from_wp_post(payload[0], response.url, source="wordpress-fallback")


def _fetch_post_from_wordpress(link: Optional[str], session: requests.Session) -> Optional[Dict[str, Any]]:
    slug = _slug_from_link(link)
    if not slug:
        return None

    response = session.get(
        f"{WP_POSTS_API_URL}?slug={quote(slug)}&_embed=1",
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if not payload:
        return None
    return _comic_from_wp_post(payload[0], response.url, source="wordpress-enrichment")


def _comic_from_wp_post(post: Dict[str, Any], api_url: str, source: str) -> Dict[str, Any]:
    pubdate = post.get("date_gmt") or post.get("date") or ""
    content_html = ((post.get("content") or {}).get("rendered") or "")
    excerpt_html = ((post.get("excerpt") or {}).get("rendered") or "")

    summary = _extract_first_paragraph(content_html) or _extract_summary(excerpt_html)
    image_url = _extract_featured_media_url(post) or _extract_image_url(content_html) or _extract_image_url(excerpt_html)

    return {
        "series": SERIES_TITLE,
        "creator": CREATOR_NAME,
        "title": _html_to_text((post.get("title") or {}).get("rendered", "")),
        "link": post.get("link", ""),
        "published_at": _normalize_iso_datetime(pubdate),
        "published_label": _format_iso_datetime(pubdate),
        "summary": summary,
        "image_url": image_url,
        "feed_url": RSS_FEED_URL,
        "api_url": api_url,
        "source": source
    }


def _download_image(session: requests.Session, image_url: str, run_dir: str) -> Tuple[str, str]:
    response = session.get(image_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    extension = mimetypes.guess_extension(content_type) or os.path.splitext(urlparse(image_url).path)[1] or ".jpg"
    image_filename = f"quantum_bits_latest{extension}"
    local_path = os.path.join(run_dir, image_filename)

    with open(local_path, "wb") as image_file:
        image_file.write(response.content)

    return image_filename, local_path


def _item_sort_key(item: ET.Element):
    try:
        return parsedate_to_datetime(_child_text(item, "pubDate"))
    except Exception:
        return parsedate_to_datetime("Thu, 01 Jan 1970 00:00:00 +0000")


def _child_text(parent: ET.Element, tag: str) -> str:
    node = parent.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def _comic_needs_enrichment(comic: Dict[str, Any]) -> bool:
    return not comic.get("summary") or not comic.get("image_url")


def _merge_comic_payloads(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(primary)
    for key, value in secondary.items():
        if key == "summary" and value:
            existing_summary = (merged.get("summary") or "").strip()
            if not existing_summary or len(existing_summary) < 80:
                merged["summary"] = value
            continue

        if not merged.get(key) and value:
            merged[key] = value
    return merged


def _extract_summary(html_snippet: str) -> str:
    if not html_snippet:
        return ""

    soup = BeautifulSoup(html_snippet, "html.parser")
    for image in soup.find_all("img"):
        image.decompose()
    for button in soup.find_all("a"):
        if "read more" in button.get_text(" ", strip=True).lower():
            button.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_first_paragraph(html_snippet: str) -> str:
    if not html_snippet:
        return ""

    soup = BeautifulSoup(html_snippet, "html.parser")
    paragraphs: List[str] = []
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        if text and "looking for a more detailed description" not in text.lower():
            paragraphs.append(text)
    return paragraphs[0] if paragraphs else ""


def _extract_image_url(html_snippet: str) -> str:
    if not html_snippet:
        return ""

    soup = BeautifulSoup(html_snippet, "html.parser")
    image = soup.find("img")
    if image is None:
        return ""
    return (image.get("src") or "").strip()


def _extract_featured_media_url(post: Dict[str, Any]) -> str:
    embedded = post.get("_embedded") or {}
    media_items = embedded.get("wp:featuredmedia") or []
    if not media_items:
        return ""

    media = media_items[0] or {}
    sizes = ((media.get("media_details") or {}).get("sizes") or {})
    full_size = sizes.get("full") or {}
    return (
        full_size.get("source_url")
        or media.get("source_url")
        or ""
    )


def _slug_from_link(link: Optional[str]) -> str:
    if not link:
        return ""

    path_parts = [part for part in urlparse(link).path.split("/") if part]
    return path_parts[-1] if path_parts else ""


def _normalize_pubdate(pubdate: str) -> str:
    try:
        return parsedate_to_datetime(pubdate).isoformat()
    except Exception:
        return ""


def _format_pubdate(pubdate: str) -> str:
    try:
        dt = parsedate_to_datetime(pubdate)
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except Exception:
        return ""


def _normalize_iso_datetime(value: str) -> str:
    if not value:
        return ""
    normalized = value.replace("Z", "+00:00")
    if "+" not in normalized[10:] and normalized.count(":") >= 2:
        normalized += "+00:00"
    return normalized


def _format_iso_datetime(value: str) -> str:
    normalized = _normalize_iso_datetime(value)
    if not normalized:
        return ""
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return ""
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
