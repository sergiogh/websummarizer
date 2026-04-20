import base64
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from html import escape
from urllib.parse import quote_plus, urlparse

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from openai import OpenAI

from api import (
    generate_global_summary,
    generate_newsletter_headline,
    generate_summary,
    generate_title,
    load_spreadsheet_data,
    process_url,
)
from blob_archive import (
    blob_archive_configured,
    is_valid_archive_path,
    list_archived_newsletters,
    load_newsletter_html,
    save_newsletter_html,
)
from image_extractor import ImageExtractor
from prompt_loader import get_prompt
from quantum_bits_comic import fetch_latest_quantum_bits_comic, resolve_comic_for_render
from qa_checks import qa_title_summary
from scientific_paper_processor import ScientificPaperProcessor
from story_organizer import (
    STORY_BUCKET_DESCRIPTIONS,
    STORY_BUCKET_LABELS,
    build_story_digest,
    curate_stories,
    group_stories,
)
from title_utils import sanitize_story_title

load_dotenv()

app = Flask(__name__)

BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD")
DEFAULT_LOOKBACK_DAYS = 7
STORY_PARSE_TIMEOUT_SECONDS = int(os.getenv("STORY_PARSE_TIMEOUT_SECONDS", "210"))
FALLBACK_FETCH_TIMEOUT_SECONDS = int(os.getenv("FALLBACK_FETCH_TIMEOUT_SECONDS", "8"))
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "dall-e-3")
OPENAI_EDIT_MODEL = os.getenv("OPENAI_EDIT_MODEL", "gpt-5")
OPENAI_EDIT_HISTORY_LIMIT = int(os.getenv("OPENAI_EDIT_HISTORY_LIMIT", "10"))
OPENAI_EDIT_MAX_BODY_CHARS = int(os.getenv("OPENAI_EDIT_MAX_BODY_CHARS", "450000"))
DEFAULT_GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1DY5RJlMcSuRGZ-0cvoK5VWoWzu4AMFh3gHglVqU9Src/export?format=csv&gid=0"


def _unauthorized() -> Response:
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Quantum Newsletter"'},
    )


def require_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not BASIC_AUTH_PASSWORD:
            return Response("Server authentication is not configured.", status=503)
        auth = request.authorization
        if not auth or auth.username != BASIC_AUTH_USERNAME or auth.password != BASIC_AUTH_PASSWORD:
            return _unauthorized()
        return view_func(*args, **kwargs)

    return wrapped


def render_summary_html(summary: str) -> str:
    rendered = escape(summary or "")
    for label in ("What happened:", "Key detail:", "Why this matters:", "Finding:", "Evidence:"):
        rendered = rendered.replace(escape(label), "<strong>%s</strong>" % label)
    return rendered.replace("\n", "<br>")


def render_comic_section(comic: dict) -> str:
    if not comic:
        return ""

    image_src = comic.get("image_src") or comic.get("image_url")
    if not image_src:
        return ""

    comic_title = escape(comic.get("title", "Latest comic strip"))
    comic_link = escape(comic.get("link", "#"), quote=True)
    comic_series = escape(comic.get("series", "Quantum Bits with Quantessa & Atomique"))
    comic_creator = escape(comic.get("creator", "Yuval Boger"))
    comic_summary = escape(comic.get("summary", ""))
    published_label = comic.get("published_label")
    published_text = (
        "Latest strip published %s" % escape(published_label)
        if published_label
        else "Latest strip"
    )
    image_src_attr = escape(image_src, quote=True)

    section = """
    <section class="comic-section">
      <div class="story-group-label">%s</div>
      <h2>%s</h2>
      <p class="comic-meta">%s · by %s</p>
      <a href="%s" target="_blank" rel="noopener noreferrer">
        <img src="%s" alt="%s" class="comic-image" />
      </a>
    """ % (
        comic_series,
        comic_title,
        published_text,
        comic_creator,
        comic_link,
        image_src_attr,
        comic_title,
    )

    if comic_summary:
        section += "<p>%s</p>" % comic_summary

    section += """
      <p class="source-link"><a href="%s" target="_blank" rel="noopener noreferrer">Read the full comic on Quantum Bits Comics</a></p>
    </section>
    """ % comic_link
    return section


def generate_cover_image_data_url(global_summary: str, headline: str) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        return ""

    prompt = get_prompt("image.highlight")
    if headline:
        prompt += "\n\nHeadline context: %s" % headline
    if global_summary:
        prompt += "\nWeekly recap context: %s" % global_summary[:1200]

    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1024",
        )
        if not getattr(response, "data", None):
            return ""

        image_item = response.data[0]
        b64_payload = getattr(image_item, "b64_json", "")
        if b64_payload:
            return "data:image/png;base64,%s" % b64_payload

        remote_url = getattr(image_item, "url", "")
        if not remote_url:
            return ""
        remote_response = requests.get(remote_url, timeout=25)
        remote_response.raise_for_status()
        content_type = (remote_response.headers.get("content-type") or "image/png").split(";")[0]
        encoded = base64.b64encode(remote_response.content).decode("ascii")
        return "data:%s;base64,%s" % (content_type, encoded)
    except Exception as exc:
        print("Warning: could not generate cover image: %s" % exc)
        return ""


def render_newsletter_html(
    headline: str,
    global_summary: str,
    results,
    overflow_results=None,
    comic: dict = None,
    cover_image_src: str = "",
):
    overflow_results = overflow_results or []
    story_groups = group_stories(results)
    sections = []
    toc_items = []

    for bucket, stories in story_groups:
        bucket_label = escape(STORY_BUCKET_LABELS.get(bucket, "Other Developments"))
        bucket_description = escape(STORY_BUCKET_DESCRIPTIONS.get(bucket, ""))
        anchor_id = "channel-%s" % bucket
        toc_items.append(
            '<a href="#%s">%s (%d)</a>' % (anchor_id, bucket_label, len(stories))
        )
        sections.append(
            """
            <section class="story-group" id="%s">
              <div class="story-group-label">%s (%d)</div>
              <p class="story-group-intent">%s</p>
            """
            % (anchor_id, bucket_label, len(stories), bucket_description)
        )
        for story in stories:
            story_url = escape(story["url"], quote=True)
            story_title = escape(story["title"])
            story_summary = render_summary_html(story["summary"])
            story_image = escape(story.get("image_url", ""), quote=True)
            image_html = ""
            if story_image:
                image_html = '<img src="%s" alt="%s" class="story-image" />' % (story_image, story_title)
            sections.append(
                """
                <article class="story-card">
                  <h3><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></h3>
                  %s
                  <p>%s</p>
                  <p class="source-link"><a href="%s" target="_blank" rel="noopener noreferrer">Read source</a></p>
                </article>
                """
                % (story_url, story_title, image_html, story_summary, story_url)
            )
        sections.append("</section>")

    overflow_section = ""
    if overflow_results:
        links = []
        for story in overflow_results:
            story_url = escape(story["url"], quote=True)
            story_title = escape(story["title"])
            links.append(
                '<li><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></li>'
                % (story_url, story_title)
            )
        overflow_section = """
        <section class="story-group overflow-group" id="more-links">
          <div class="story-group-label">More links this week (%d)</div>
          <ul class="overflow-list">%s</ul>
        </section>
        """ % (len(overflow_results), "".join(links))

    table_of_contents = ""
    if toc_items:
        table_of_contents = """
        <nav class="toc">
          <div class="toc-title">Topics in this issue</div>
          <div class="toc-links">%s</div>
        </nav>
        """ % "".join(toc_items)

    comic_section = render_comic_section(comic)
    cover_image_html = ""
    if cover_image_src:
        cover_image_html = '<img src="%s" alt="%s" class="highlight-image" />' % (
            escape(cover_image_src, quote=True),
            escape(headline),
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{headline}</title>
  <style>
    :root {{
      --ink: #122033;
      --muted: #516174;
      --line: #d6e1ec;
      --paper: #f7fbff;
      --card: #ffffff;
      --accent: #0f766e;
      --link: #1859c9;
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.08), transparent 34%),
        linear-gradient(180deg, #f8fbfd 0%, #eef4f8 100%);
      color: var(--ink);
    }}
    main {{
      max-width: 900px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .masthead {{
      background: rgba(255,255,255,0.78);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 28px;
      box-shadow: 0 16px 40px rgba(18, 32, 51, 0.08);
      backdrop-filter: blur(8px);
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      margin-bottom: 10px;
      font-family: Arial, sans-serif;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2rem, 4vw, 3.2rem);
      line-height: 1.05;
    }}
    .meta {{
      margin-top: 14px;
      color: var(--muted);
      font-family: Arial, sans-serif;
    }}
    .recap {{
      margin-top: 22px;
      font-size: 1.05rem;
      line-height: 1.7;
    }}
    .highlight-image {{
      width: 100%;
      border-radius: 14px;
      margin-top: 18px;
      max-height: 440px;
      object-fit: cover;
      box-shadow: 0 8px 24px rgba(18, 32, 51, 0.08);
    }}
    .story-group {{
      margin-top: 34px;
    }}
    .story-group-intent {{
      margin: 0 0 12px;
      color: var(--muted);
      font-family: Arial, sans-serif;
      font-size: 0.92rem;
      line-height: 1.5;
    }}
    .story-group-label {{
      margin-bottom: 14px;
      color: var(--accent);
      font-family: Arial, sans-serif;
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .story-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 20px;
      margin-bottom: 14px;
      box-shadow: 0 8px 24px rgba(18, 32, 51, 0.05);
    }}
    .story-card h3 {{
      margin: 0 0 10px;
      font-size: 1.2rem;
      line-height: 1.3;
    }}
    .story-card p {{
      margin: 0 0 10px;
      line-height: 1.65;
    }}
    .story-image {{
      width: 100%;
      border-radius: 10px;
      margin: 8px 0 12px;
      max-height: 280px;
      object-fit: cover;
      border: 1px solid var(--line);
    }}
    .comic-section {{
      margin-top: 34px;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 20px;
      box-shadow: 0 8px 24px rgba(18, 32, 51, 0.05);
    }}
    .comic-image {{
      width: 100%;
      border-radius: 10px;
      margin: 6px 0 12px;
      border: 1px solid var(--line);
    }}
    .comic-meta {{
      margin-top: -4px;
      color: var(--muted);
      font-family: Arial, sans-serif;
      font-size: 0.92rem;
    }}
    .source-link {{
      font-family: Arial, sans-serif;
      font-size: 0.92rem;
      color: var(--muted);
    }}
    .issue-stats {{
      margin-top: 14px;
      font-family: Arial, sans-serif;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .toc {{
      margin-top: 18px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.75);
    }}
    .toc-title {{
      font-family: Arial, sans-serif;
      font-size: 0.8rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 8px;
      font-weight: 700;
    }}
    .toc-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      font-family: Arial, sans-serif;
      font-size: 0.92rem;
    }}
    .overflow-group {{
      border-top: 1px dashed var(--line);
      padding-top: 20px;
    }}
    .overflow-list {{
      margin: 0;
      padding-left: 18px;
      line-height: 1.75;
    }}
    .overflow-list li {{
      margin-bottom: 4px;
    }}
    a {{
      color: var(--link);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
  </style>
</head>
<body>
  <main>
    <header class="masthead">
      <div class="eyebrow">Weekly Quantum Newsletter</div>
      <h1>{headline}</h1>
      <div class="meta">Generated {generated_at}</div>
      <div class="issue-stats">This week: {primary_count} selected stories</div>
      <div class="recap">{global_summary}</div>
      {table_of_contents}
      {cover_image_html}
    </header>
    {comic_section}
    {sections}
    {overflow_section}
  </main>
</body>
</html>
""".format(
        headline=escape(headline),
        generated_at=generated_at,
        primary_count=len(results),
        global_summary=escape(global_summary),
        table_of_contents=table_of_contents,
        cover_image_html=cover_image_html,
        comic_section=comic_section,
        sections="".join(sections),
        overflow_section=overflow_section,
    )


def prepare_runtime_env(api_key: str = "", sheet_url: str = "", require_openai: bool = True):
    if not os.getenv("GOOGLE_SHEET"):
        os.environ["GOOGLE_SHEET"] = sheet_url or DEFAULT_GOOGLE_SHEET_URL

    if require_openai and not os.getenv("OPENAI_API_KEY"):
        if not api_key:
            raise RuntimeError("OpenAI API key is required.")
        os.environ["OPENAI_API_KEY"] = api_key


def resolve_date_range(start_raw: str = "", end_raw: str = "", days: int = DEFAULT_LOOKBACK_DAYS):
    now_local = datetime.now()
    start_raw = (start_raw or "").strip()
    end_raw = (end_raw or "").strip()

    if start_raw or end_raw:
        if not start_raw or not end_raw:
            raise RuntimeError("Both start date and end date are required.")
        try:
            start_dt = datetime.strptime(start_raw, "%Y-%m-%d")
            end_dt = datetime.strptime(end_raw, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
        except ValueError as exc:
            raise RuntimeError("Invalid date format. Use YYYY-MM-DD.") from exc
    else:
        end_dt = now_local
        start_dt = end_dt - timedelta(days=days)

    if start_dt > end_dt:
        raise RuntimeError("Start date must be before end date.")

    return start_dt, end_dt


def remove_publisher_mentions(title: str, url: str = "") -> str:
    cleaned = title or ""
    host = ""
    if url:
        host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
        if host.startswith("www."):
            host = host[4:]

    domain_candidates = []
    if host:
        domain_candidates.append(host)
        if "." in host:
            domain_candidates.append(host.split(".")[0])

    for domain in domain_candidates:
        cleaned = re.sub(
            r"(?i)\b(?:from|via|by)\s+%s\b" % re.escape(domain),
            "",
            cleaned,
        )
        cleaned = re.sub(r"(?i)\b%s\b" % re.escape(domain), "", cleaned)

    cleaned = re.sub(
        r"(?i)\s*[-|–—:]\s*[A-Za-z0-9.-]+\.(?:com|io|org|net|co|ai|edu|gov)\b",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(?:from|via|by)\s+[A-Za-z0-9.-]+\.(?:com|io|org|net|co|ai|edu|gov)\b",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|–—:")
    return cleaned or (title or "")


def extract_quick_text(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=FALLBACK_FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        text = response.text or ""
        if "html" in content_type.lower():
            text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
            text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:500]
    except Exception:
        return ""


def build_fallback_story(index: int, url: str, title_seed: str, tag: str, reason: str = ""):
    paper_processor = ScientificPaperProcessor(url)
    is_paper = paper_processor.is_scientific_paper()
    title = title_seed.strip() if title_seed and title_seed.strip() else "Untitled story"
    title = sanitize_story_title(title, is_paper=is_paper)
    title = remove_publisher_mentions(title, url)

    quick_text = extract_quick_text(url)
    summary_parts = [
        "Fallback summary: source parsing exceeded the processing budget, so this item uses available metadata.",
    ]
    if reason:
        summary_parts.append(f"Reason: {reason}.")
    if quick_text:
        summary_parts.append(f"Quick extracted text: {quick_text}")
    else:
        summary_parts.append(f"Source URL: {url}")

    fallback_image = ""
    try:
        image_extractor = ImageExtractor(url)
        image_extractor.extract_image()
        fallback_image = image_extractor.image_url or ""
    except Exception:
        fallback_image = ""

    qa_result = qa_title_summary(title, " ".join(summary_parts))
    return {
        "story_id": str(index),
        "url": url,
        "title": qa_result["title_fixed"],
        "summary": qa_result["summary_fixed"],
        "image_url": fallback_image,
        "tag": tag,
        "is_paper": is_paper,
        "paper_type": paper_processor.paper_type,
        "is_fallback": True,
    }


def process_story_with_timeout(index: int, url: str, title_seed: str, tag: str, timeout_seconds: int):
    container = {"story": None, "error": None}
    done = threading.Event()

    def _worker():
        try:
            container["story"] = process_story(index, url, title_seed, tag)
        except Exception as exc:
            container["error"] = str(exc)
        finally:
            done.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    done.wait(timeout=timeout_seconds)

    if not done.is_set():
        return None, f"Timed out after {timeout_seconds}s"
    if container["error"]:
        return None, container["error"]
    return container["story"], ""


def _emit_progress(progress_callback, payload):
    if progress_callback:
        progress_callback(payload)


def process_story(index: int, url: str, title_seed: str, tag: str):
    paper_processor = ScientificPaperProcessor(url)
    is_paper = paper_processor.is_scientific_paper()
    content_bundle = process_url(url, title_seed, "")
    summary = generate_summary(title_seed, content_bundle["clean"], url)
    if not summary:
        return None

    rewritten_title = generate_title(summary, url, is_paper, source_title=title_seed)
    if not rewritten_title or not rewritten_title.strip():
        rewritten_title = title_seed
    rewritten_title = sanitize_story_title(rewritten_title, is_paper=is_paper)
    rewritten_title = remove_publisher_mentions(rewritten_title, url)
    if not rewritten_title or not rewritten_title.strip():
        rewritten_title = remove_publisher_mentions(sanitize_story_title(title_seed, is_paper=is_paper), url)
    qa_result = qa_title_summary(rewritten_title, summary)

    article_image = ""
    try:
        image_extractor = ImageExtractor(url)
        image_extractor.extract_image()
        article_image = image_extractor.image_url or ""
    except Exception:
        article_image = ""

    return {
        "story_id": str(index),
        "url": url,
        "title": qa_result["title_fixed"],
        "summary": qa_result["summary_fixed"],
        "image_url": article_image,
        "tag": tag,
        "is_paper": bool(content_bundle["is_paper"] or is_paper),
        "paper_type": content_bundle["paper_type"],
        "is_fallback": False,
    }


def _normalize_additional_links(items):
    normalized = []
    seen_urls = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        url_key = url.lower()
        if url_key in seen_urls:
            continue
        raw_title = str(item.get("title_seed") or item.get("title") or "").strip()
        clean_title = remove_publisher_mentions(
            sanitize_story_title(raw_title or url, is_paper=False),
            url,
        )
        if not clean_title:
            clean_title = url
        normalized.append({"url": url, "title": clean_title})
        seen_urls.add(url_key)
    return normalized


def finalize_newsletter(results, additional_links=None):
    if not results:
        raise RuntimeError("No newsletter stories were generated.")

    curated = curate_stories(results)
    primary_results = curated["primary"]
    overflow_results = curated["overflow"]
    manual_additional_links = _normalize_additional_links(additional_links)
    primary_urls = {str(story.get("url") or "").strip().lower() for story in primary_results if story.get("url")}
    merged_overflow = []
    seen_overflow_urls = set(primary_urls)
    for story in manual_additional_links + overflow_results:
        story_url = str(story.get("url") or "").strip()
        if not story_url:
            continue
        url_key = story_url.lower()
        if url_key in seen_overflow_urls:
            continue
        merged_overflow.append(story)
        seen_overflow_urls.add(url_key)
    overflow_results = merged_overflow

    digest = build_story_digest(primary_results)
    global_summary = generate_global_summary(digest) or "Weekly newsletter generated."
    headline = generate_newsletter_headline(global_summary) or "Weekly Quantum Newsletter"
    cover_image_src = generate_cover_image_data_url(global_summary, headline)

    comic = None
    try:
        run_dir = "/tmp/websummarizer_comic"
        os.makedirs(run_dir, exist_ok=True)
        comic_data = fetch_latest_quantum_bits_comic(run_dir)
        if comic_data:
            comic = resolve_comic_for_render(comic_data, comic_data.get("image_url"))
    except Exception as exc:
        print("Warning: could not load comic strip: %s" % exc)

    html_content = render_newsletter_html(
        headline,
        global_summary,
        primary_results,
        overflow_results=overflow_results,
        comic=comic,
        cover_image_src=cover_image_src,
    )

    return {
        "headline": headline,
        "global_summary": global_summary,
        "results": primary_results,
        "overflow_results": overflow_results,
        "channel_counts": curated["channel_counts"],
        "html": html_content,
        "comic": comic,
        "cover_image_src": cover_image_src,
    }


def build_weekly_newsletter(
    days: int = DEFAULT_LOOKBACK_DAYS,
    api_key: str = "",
    sheet_url: str = "",
    progress_callback=None,
):
    prepare_runtime_env(api_key=api_key, sheet_url=sheet_url)
    _emit_progress(progress_callback, {"phase": "load", "message": "Loading stories from sheet..."})
    spreadsheet_handler = load_spreadsheet_data(days)
    results = []
    total_stories = len(spreadsheet_handler.urls)
    parsed_count = 0
    skipped_count = 0

    _emit_progress(
        progress_callback,
        {
            "phase": "load_complete",
            "message": "Loaded %d stories from sheet." % total_stories,
            "total": total_stories,
            "scanned": 0,
            "parsed": 0,
            "skipped": 0,
        },
    )

    for index, url in enumerate(spreadsheet_handler.urls):
        scanned_count = index + 1
        title_seed = spreadsheet_handler.titles[index]
        tag = spreadsheet_handler.tags[index]
        _emit_progress(
            progress_callback,
            {
                "phase": "story_start",
                "message": "Parsing story %d of %d" % (scanned_count, total_stories),
                "total": total_stories,
                "scanned": scanned_count,
                "parsed": parsed_count,
                "skipped": skipped_count,
                "url": url,
            },
        )
        story, failure_reason = process_story_with_timeout(
            index, url, title_seed, tag, STORY_PARSE_TIMEOUT_SECONDS
        )
        if not story:
            story = build_fallback_story(index, url, title_seed, tag, reason=failure_reason or "No summary generated")
            skipped_count += 1
            _emit_progress(
                progress_callback,
                {
                    "phase": "story_fallback",
                    "message": "Used fallback for story %d" % scanned_count,
                    "total": total_stories,
                    "scanned": scanned_count,
                    "parsed": parsed_count,
                    "skipped": skipped_count,
                    "url": url,
                },
            )
        results.append(story)
        parsed_count += 1
        _emit_progress(
            progress_callback,
            {
                "phase": "story_done",
                "message": "Parsed story %d of %d" % (scanned_count, total_stories),
                "total": total_stories,
                "scanned": scanned_count,
                "parsed": parsed_count,
                "skipped": skipped_count,
                "url": url,
            },
        )

    _emit_progress(
        progress_callback,
        {
            "phase": "organize",
            "message": "Organizing stories and generating final newsletter...",
            "total": total_stories,
            "scanned": total_stories,
            "parsed": parsed_count,
            "skipped": skipped_count,
        },
    )
    newsletter = finalize_newsletter(results)
    newsletter.update(
        {
            "total": total_stories,
            "parsed": parsed_count,
            "skipped": skipped_count,
        }
    )
    return newsletter


def format_archive_timestamp(value) -> str:
    if not value:
        return "Unknown date"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def decode_html_payload(raw_html) -> str:
    if raw_html is None:
        return ""
    if isinstance(raw_html, str):
        return raw_html
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw_html.decode(encoding)
        except Exception:
            continue
    try:
        return raw_html.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _strip_markdown_fence(text: str) -> str:
    cleaned = (text or "").strip()
    fenced = re.match(r"^```(?:json|html)?\s*([\s\S]*?)\s*```$", cleaned, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return cleaned


def parse_html_edit_response(raw_text: str, fallback_html: str):
    cleaned = _strip_markdown_fence(raw_text)
    if not cleaned:
        return fallback_html, "Applied your requested edits."

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            updated_html = parsed.get("updated_html") or parsed.get("html") or ""
            assistant_message = parsed.get("assistant_message") or parsed.get("message") or ""
            if isinstance(updated_html, str) and updated_html.strip():
                return updated_html, (assistant_message or "Applied your requested edits.")
    except Exception:
        pass

    html_match = re.search(r"\bHTML:\s*([\s\S]+)$", cleaned, flags=re.IGNORECASE)
    if html_match:
        candidate = html_match.group(1).strip()
        if candidate:
            return candidate, "Applied your requested edits."

    if "<html" in cleaned.lower() or cleaned.lstrip().lower().startswith("<!doctype"):
        return cleaned, "Applied your requested edits."

    return fallback_html, cleaned[:220]


def parse_body_edit_response(raw_text: str, fallback_body: str):
    cleaned = _strip_markdown_fence(raw_text)
    if not cleaned:
        return fallback_body, "Applied your requested edits."

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            updated_body = parsed.get("updated_body_html") or parsed.get("body_html") or ""
            assistant_message = parsed.get("assistant_message") or parsed.get("message") or ""
            if isinstance(updated_body, str) and updated_body.strip():
                return updated_body, (assistant_message or "Applied your requested edits.")
    except Exception:
        pass

    if "<" in cleaned:
        return cleaned, "Applied your requested edits."
    return fallback_body, cleaned[:220]


_DATA_URI_RE = re.compile(r"data:[^\"'\s>]+;base64,[A-Za-z0-9+/=\s]+", flags=re.IGNORECASE)


def redact_inline_data_uris(html_content: str):
    if not html_content:
        return "", {}, 0

    replacements = {}

    def _replace(match):
        token = "__INLINE_DATA_URI_%05d__" % (len(replacements) + 1)
        replacements[token] = match.group(0)
        return token

    redacted = _DATA_URI_RE.sub(_replace, html_content)
    return redacted, replacements, len(replacements)


def restore_inline_data_uris(html_content: str, replacements):
    restored = html_content or ""
    for token, value in (replacements or {}).items():
        restored = restored.replace(token, value)
    return restored


def extract_body_segments(html_content: str):
    match = re.search(r"(<body\b[^>]*>)([\s\S]*?)(</body>)", html_content or "", flags=re.IGNORECASE)
    if not match:
        return None
    prefix = (html_content or "")[: match.start(2)]
    body = match.group(2)
    suffix = (html_content or "")[match.end(2) :]
    return prefix, body, suffix


def is_request_too_large_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "request too large" in message
        or ("tokens per min" in message and "requested" in message and "limit" in message)
        or ("rate_limit_exceeded" in message and "tokens" in message)
    )


ARCHIVE_EDITOR_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Newsletter Edition Mode</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/codemirror@5.65.16/lib/codemirror.min.css">
  <style>
    :root {
      --ink: #162233;
      --muted: #5f738b;
      --line: #d4e1ee;
      --panel: #ffffff;
      --canvas: #f3f7fb;
      --accent: #0f766e;
      --chat: #0b1729;
      --chat-muted: #8fa4bc;
      --chat-user: #1e3a8a;
      --chat-assistant: #10223f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(15,118,110,0.12), transparent 34%),
        radial-gradient(circle at bottom left, rgba(28,99,213,0.1), transparent 26%),
        linear-gradient(180deg, #f9fbff 0%, #ecf2f8 100%);
    }
    .editor-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
      min-height: 100vh;
      padding: 16px;
    }
    @media (max-width: 1180px) {
      .editor-layout {
        grid-template-columns: 1fr;
      }
      .chat-sidebar {
        min-height: 420px;
      }
    }
    .canvas-panel {
      background: rgba(255,255,255,0.86);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      box-shadow: 0 18px 38px rgba(16, 31, 55, 0.08);
      backdrop-filter: blur(8px);
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 14px;
      min-height: 0;
    }
    .canvas-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      flex-wrap: wrap;
    }
    .canvas-header h1 {
      margin: 0;
      font-size: 1.25rem;
    }
    .meta {
      margin-top: 5px;
      color: var(--muted);
      font-size: 0.92rem;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    button {
      border: none;
      border-radius: 999px;
      background: linear-gradient(135deg, #0f766e 0%, #1250a8 100%);
      color: #fff;
      font-size: 0.9rem;
      font-weight: 700;
      padding: 9px 14px;
      cursor: pointer;
    }
    button.secondary {
      background: #fff;
      color: #184870;
      border: 1px solid #9ac7c3;
    }
    .workspace-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      min-height: 0;
      height: calc(100vh - 120px);
    }
    @media (max-width: 1180px) {
      .workspace-grid {
        grid-template-columns: 1fr;
        height: auto;
      }
    }
    .editor-pane,
    .preview-pane {
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      background: var(--panel);
      min-height: 380px;
      display: flex;
      flex-direction: column;
    }
    .pane-label {
      border-bottom: 1px solid var(--line);
      font-size: 0.82rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
      padding: 8px 10px;
      background: var(--canvas);
    }
    .CodeMirror {
      height: 100%;
      font-size: 13px;
      font-family: "JetBrains Mono", "SFMono-Regular", Menlo, monospace;
    }
    textarea#html-editor {
      width: 100%;
      height: 100%;
      border: none;
      padding: 10px;
      font-family: "JetBrains Mono", "SFMono-Regular", Menlo, monospace;
      font-size: 13px;
      resize: vertical;
    }
    #live-preview {
      width: 100%;
      height: 100%;
      border: none;
      background: #ffffff;
      flex: 1;
    }
    .chat-sidebar {
      background: var(--chat);
      color: #f2f6fb;
      border-radius: 18px;
      border: 1px solid rgba(148, 184, 226, 0.22);
      padding: 14px;
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 10px;
      min-height: 0;
      box-shadow: 0 20px 44px rgba(9, 16, 28, 0.34);
    }
    .chat-header h2 {
      margin: 0;
      font-size: 1.02rem;
    }
    .chat-note {
      margin-top: 4px;
      color: var(--chat-muted);
      font-size: 0.86rem;
      line-height: 1.45;
    }
    .chat-key {
      display: none;
      gap: 6px;
    }
    .chat-key.visible {
      display: grid;
    }
    .chat-key label {
      font-size: 0.82rem;
      color: var(--chat-muted);
    }
    .chat-key input {
      width: 100%;
      border-radius: 10px;
      border: 1px solid rgba(148, 184, 226, 0.35);
      background: #0f1d33;
      color: #eff6ff;
      padding: 10px;
      font-size: 0.95rem;
    }
    .chat-log {
      border: 1px solid rgba(148, 184, 226, 0.25);
      border-radius: 12px;
      padding: 10px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 8px;
      background: rgba(8, 18, 34, 0.88);
      min-height: 240px;
      max-height: calc(100vh - 360px);
    }
    .chat-message {
      border-radius: 12px;
      padding: 8px 10px;
      font-size: 0.92rem;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .chat-message.user {
      background: var(--chat-user);
      align-self: flex-end;
    }
    .chat-message.assistant {
      background: var(--chat-assistant);
      border: 1px solid rgba(148, 184, 226, 0.22);
      align-self: stretch;
    }
    .chat-form {
      display: grid;
      gap: 8px;
    }
    .chat-form textarea {
      width: 100%;
      resize: vertical;
      min-height: 84px;
      max-height: 220px;
      border-radius: 12px;
      border: 1px solid rgba(148, 184, 226, 0.35);
      padding: 10px;
      background: #0f1d33;
      color: #f0f6ff;
      font-size: 0.95rem;
      font-family: inherit;
    }
    .chat-form button {
      justify-self: end;
    }
  </style>
</head>
<body>
  <div class="editor-layout">
    <section class="canvas-panel">
      <header class="canvas-header">
        <div>
          <h1 id="document-name">Newsletter Edition Mode</h1>
          <div class="meta" id="document-meta"></div>
        </div>
        <div class="toolbar">
          <button type="button" class="secondary" id="reset-button">Reset</button>
          <button type="button" class="secondary" id="apply-preview-button">Apply Preview</button>
          <button type="button" class="secondary" id="open-tab-button">Open Render</button>
          <button type="button" id="download-button">Download HTML</button>
        </div>
      </header>
      <div class="workspace-grid">
        <section class="editor-pane">
          <div class="pane-label">HTML Source</div>
          <textarea id="html-editor"></textarea>
        </section>
        <section class="preview-pane">
          <div class="pane-label">Live Preview</div>
          <iframe id="live-preview" title="Newsletter preview"></iframe>
        </section>
      </div>
    </section>
    <aside class="chat-sidebar">
      <header class="chat-header">
        <h2>AI Edit Assistant</h2>
        <div class="chat-note" id="model-label"></div>
      </header>
      <div class="chat-key" id="chat-key">
        <label for="chat-api-key" id="chat-api-key-label">OpenAI API key (optional override)</label>
        <input id="chat-api-key" type="password" autocomplete="off" placeholder="sk-..." />
      </div>
      <div id="chat-log" class="chat-log"></div>
      <form id="chat-form" class="chat-form">
        <textarea id="chat-input" placeholder="Ask for precise HTML changes, visual tweaks, rewrites, or layout updates."></textarea>
        <button type="submit" id="chat-send">Send</button>
      </form>
    </aside>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/codemirror@5.65.16/lib/codemirror.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/codemirror@5.65.16/mode/xml/xml.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/codemirror@5.65.16/mode/javascript/javascript.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/codemirror@5.65.16/mode/css/css.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/codemirror@5.65.16/mode/htmlmixed/htmlmixed.min.js"></script>

  <script>
    const bootstrap = __EDITOR_BOOTSTRAP__;
    const textarea = document.getElementById('html-editor');
    const previewFrame = document.getElementById('live-preview');
    const chatLog = document.getElementById('chat-log');
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatSend = document.getElementById('chat-send');
    const chatKeyContainer = document.getElementById('chat-key');
    const chatApiKey = document.getElementById('chat-api-key');
    const chatApiKeyLabel = document.getElementById('chat-api-key-label');
    const modelLabel = document.getElementById('model-label');
    const resetButton = document.getElementById('reset-button');
    const applyPreviewButton = document.getElementById('apply-preview-button');
    const downloadButton = document.getElementById('download-button');
    const openTabButton = document.getElementById('open-tab-button');
    const documentName = document.getElementById('document-name');
    const documentMeta = document.getElementById('document-meta');

    const chatMessages = [];
    let editor = null;
    let originalHtml = String(bootstrap.initial_html || '');
    let activeFilename = String(bootstrap.filename || 'newsletter.html');

    function escapeHtml(value) {
      return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function addChatMessage(role, text) {
      const safeRole = role === 'user' ? 'user' : 'assistant';
      const content = String(text || '').trim();
      if (!content) return;
      chatMessages.push({ role: safeRole, content: content });
      const row = document.createElement('div');
      row.className = 'chat-message ' + safeRole;
      row.innerHTML = escapeHtml(content);
      chatLog.appendChild(row);
      chatLog.scrollTop = chatLog.scrollHeight;
    }

    function getEditorValue() {
      return editor ? editor.getValue() : textarea.value;
    }

    function setEditorValue(value) {
      if (editor) {
        editor.setValue(String(value || ''));
      } else {
        textarea.value = String(value || '');
      }
    }

    function refreshPreview() {
      previewFrame.srcdoc = getEditorValue();
    }

    function loadBrowserArchiveIfNeeded() {
      if (String(bootstrap.source || '') !== 'browser') {
        return;
      }
      const token = String(bootstrap.token || '');
      if (!token) {
        addChatMessage('assistant', 'Missing local browser archive token for this editing session.');
        return;
      }
      try {
        const raw = localStorage.getItem(token);
        if (!raw) {
          addChatMessage('assistant', 'Could not find the browser-saved newsletter for this token.');
          return;
        }
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed.html === 'string') {
          originalHtml = parsed.html;
          if (parsed.filename) {
            activeFilename = String(parsed.filename);
          }
        }
      } catch (error) {
        addChatMessage('assistant', 'Failed to load browser archive: ' + (error.message || 'Unknown error'));
      }
    }

    function openRenderedTab() {
      const blob = new Blob([getEditorValue()], { type: 'text/html' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
    }

    function downloadHtml() {
      const blob = new Blob([getEditorValue()], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = activeFilename || 'newsletter_edited.html';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
    }

    function initEditor() {
      if (window.CodeMirror && window.CodeMirror.fromTextArea) {
        editor = window.CodeMirror.fromTextArea(textarea, {
          mode: 'htmlmixed',
          lineNumbers: true,
          lineWrapping: true,
          indentUnit: 2,
          tabSize: 2,
        });
        editor.on('changes', function () {
          refreshPreview();
        });
      }
    }

    async function applyPrompt(prompt) {
      const apiKey = chatApiKey ? String(chatApiKey.value || '').trim() : '';
      if (bootstrap.requires_api_key && !apiKey) {
        addChatMessage('assistant', 'Add your OpenAI API key first so I can apply this edit.');
        return;
      }

      chatSend.disabled = true;
      try {
        const response = await fetch('/archive/edit/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            html: getEditorValue(),
            instruction: prompt,
            openai_api_key: apiKey,
            messages: chatMessages.slice(-10),
          }),
        });
        let payload = {};
        try {
          payload = await response.json();
        } catch (error) {
          payload = { ok: false, error: 'Failed to read editor response.' };
        }
        if (!response.ok || payload.ok === false) {
          throw new Error(payload.error || ('Request failed with status ' + response.status));
        }
        if (typeof payload.updated_html === 'string' && payload.updated_html.trim()) {
          setEditorValue(payload.updated_html);
          refreshPreview();
        }
        addChatMessage('assistant', payload.assistant_message || 'Applied your requested edit.');
      } catch (error) {
        addChatMessage('assistant', 'Edit failed: ' + (error.message || 'Unknown error'));
      } finally {
        chatSend.disabled = false;
      }
    }

    loadBrowserArchiveIfNeeded();
    initEditor();

    if (!originalHtml.trim()) {
      originalHtml = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Newsletter</title></head><body><h1>No newsletter content loaded.</h1></body></html>';
    }
    setEditorValue(originalHtml);
    refreshPreview();

    documentName.textContent = 'Edition Mode: ' + activeFilename;
    documentMeta.textContent = String(bootstrap.source || 'server') === 'browser'
      ? 'Loaded from browser archive'
      : 'Loaded from server archive';
    modelLabel.textContent = 'Model: ' + String(bootstrap.model || 'gpt-5') + '. Prompts edit the full HTML and update preview live.';

    chatKeyContainer.classList.add('visible');
    if (bootstrap.requires_api_key) {
      if (chatApiKeyLabel) {
        chatApiKeyLabel.textContent = 'OpenAI API key (required)';
      }
      if (chatApiKey) {
        chatApiKey.placeholder = 'sk-... (required)';
      }
    }

    addChatMessage('assistant', 'Share what to change. I will update the HTML and refresh the preview.');

    chatForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      const prompt = String(chatInput.value || '').trim();
      if (!prompt) return;
      chatInput.value = '';
      addChatMessage('user', prompt);
      await applyPrompt(prompt);
    });

    resetButton.addEventListener('click', function () {
      setEditorValue(originalHtml);
      refreshPreview();
      addChatMessage('assistant', 'Reset to the original archived HTML.');
    });

    applyPreviewButton.addEventListener('click', function () {
      refreshPreview();
    });

    openTabButton.addEventListener('click', function () {
      openRenderedTab();
    });

    downloadButton.addEventListener('click', function () {
      downloadHtml();
    });
  </script>
</body>
</html>
"""


def render_archive_editor_html(bootstrap_payload: dict) -> str:
    return ARCHIVE_EDITOR_TEMPLATE.replace(
        "__EDITOR_BOOTSTRAP__", json.dumps(bootstrap_payload, ensure_ascii=False)
    )


@app.route("/")
@require_auth
def home():
    archives = list_archived_newsletters()

    if archives:
        archive_rows = []
        for item in archives:
            archive_rows.append(
                """
                <li class="archive-item">
                  <div>
                    <div class="archive-name">{name}</div>
                    <div class="archive-meta">{uploaded_at}</div>
                  </div>
                  <div class="archive-actions">
                    <a class="archive-link" href="/archive?pathname={pathname}" target="_blank" rel="noopener">Open</a>
                    <a class="archive-link" href="/archive/edit?pathname={pathname}" target="_blank" rel="noopener">Edit</a>
                  </div>
                </li>
                """.format(
                    name=escape(item["filename"]),
                    uploaded_at=escape(format_archive_timestamp(item["uploaded_at"])),
                    pathname=quote_plus(item["pathname"]),
                )
            )
        archives_html = "<ul class=\"archive-list\">%s</ul>" % "".join(archive_rows)
    else:
        archives_html = "<div class=\"empty-state\">No generated newsletters yet.</div>"

    storage_message = ""
    if not blob_archive_configured():
        storage_message = (
            "<div class=\"warning\">Server archive is not configured yet. Generated newsletters will be stored in this browser until <code>BLOB_READ_WRITE_TOKEN</code> is configured in Vercel.</div>"
        )

    key_fields = ""
    if not os.getenv("OPENAI_API_KEY"):
        key_fields = """
          <label class="field">
            <span>OpenAI API key</span>
            <input type="password" name="openai_api_key" autocomplete="off" placeholder="sk-..." required />
          </label>
        """
    else:
        key_fields = "<div class=\"note\">Using the server-side OpenAI API key configured in the deployment.</div>"

    today = datetime.now().strftime("%Y-%m-%d")
    default_start = (datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Quantum Newsletter Control Room</title>
      <style>
        :root {{
          --ink: #132238;
          --muted: #5e7088;
          --line: #d4e2ef;
          --panel: rgba(255,255,255,0.88);
          --accent: #0f766e;
          --link: #1c63d5;
          --danger: #991b1b;
          --danger-bg: #fef2f2;
          --success: #166534;
          --success-bg: #f0fdf4;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          min-height: 100vh;
          font-family: Arial, sans-serif;
          color: var(--ink);
          background:
            radial-gradient(circle at top right, rgba(15,118,110,0.12), transparent 28%),
            radial-gradient(circle at bottom left, rgba(28,99,213,0.1), transparent 24%),
            linear-gradient(180deg, #f8fbfd 0%, #ecf3f8 100%);
        }}
        main {{
          max-width: 920px;
          margin: 0 auto;
          padding: 28px 20px 48px;
        }}
        .hero {{
          background: var(--panel);
          border: 1px solid var(--line);
          border-radius: 20px;
          padding: 28px;
          box-shadow: 0 18px 40px rgba(19, 34, 56, 0.08);
          backdrop-filter: blur(10px);
        }}
        .eyebrow {{
          color: var(--accent);
          font-size: 0.8rem;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          margin-bottom: 10px;
        }}
        h1 {{
          margin: 0;
          font-size: clamp(2rem, 4vw, 3rem);
          line-height: 1.05;
        }}
        .lead {{
          margin-top: 14px;
          color: var(--muted);
          max-width: 680px;
          line-height: 1.6;
        }}
        .controls {{
          margin-top: 22px;
          display: grid;
          gap: 14px;
        }}
        .field-row {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
          max-width: 640px;
        }}
        @media (max-width: 700px) {{
          .field-row {{
            grid-template-columns: 1fr;
          }}
        }}
        .actions {{
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
        }}
        button {{
          border: none;
          border-radius: 999px;
          background: linear-gradient(135deg, #0f766e 0%, #1250a8 100%);
          color: white;
          padding: 12px 20px;
          font-size: 1rem;
          font-weight: 700;
          cursor: pointer;
          box-shadow: 0 12px 24px rgba(15,118,110,0.2);
        }}
        .secondary-button {{
          background: white;
          color: #134e4a;
          border: 1px solid #9ac7c3;
          box-shadow: none;
        }}
        .note {{
          color: var(--muted);
          font-size: 0.95rem;
        }}
        .field {{
          display: grid;
          gap: 6px;
          max-width: 420px;
        }}
        .field span {{
          font-size: 0.92rem;
          font-weight: 700;
        }}
        .field input {{
          width: 100%;
          padding: 12px 14px;
          border: 1px solid var(--line);
          border-radius: 12px;
          font-size: 1rem;
          background: white;
          color: var(--ink);
        }}
        .banner {{
          margin-top: 18px;
          padding: 14px 16px;
          border-radius: 14px;
          border: 1px solid var(--line);
        }}
        .banner.success {{
          background: var(--success-bg);
          color: var(--success);
          border-color: #bbf7d0;
        }}
        .banner.error {{
          background: var(--danger-bg);
          color: var(--danger);
          border-color: #fecaca;
        }}
        .warning {{
          margin-top: 18px;
          background: #fff7ed;
          border: 1px solid #fed7aa;
          color: #9a3412;
          padding: 14px 16px;
          border-radius: 14px;
        }}
        .archive-panel {{
          margin-top: 24px;
          background: var(--panel);
          border: 1px solid var(--line);
          border-radius: 20px;
          padding: 24px;
          box-shadow: 0 18px 40px rgba(19, 34, 56, 0.06);
        }}
        .archive-header {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: baseline;
          margin-bottom: 18px;
        }}
        .archive-header h2 {{
          margin: 0;
        }}
        .archive-list {{
          list-style: none;
          margin: 0;
          padding: 0;
        }}
        .archive-item {{
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: center;
          padding: 14px 0;
          border-top: 1px solid var(--line);
        }}
        .archive-item:first-child {{
          border-top: none;
        }}
        .archive-name {{
          font-weight: 700;
        }}
        .archive-meta {{
          margin-top: 4px;
          color: var(--muted);
          font-size: 0.92rem;
        }}
        .archive-link {{
          color: var(--link);
          text-decoration: none;
          font-weight: 700;
        }}
        .archive-link:hover {{
          text-decoration: underline;
        }}
        .archive-actions {{
          display: flex;
          align-items: center;
          gap: 12px;
          flex-wrap: wrap;
        }}
        .browser-open {{
          border: none;
          background: transparent;
          color: var(--link);
          font: inherit;
          font-weight: 700;
          cursor: pointer;
          padding: 0;
        }}
        .browser-open:hover {{
          text-decoration: underline;
        }}
        .empty-state {{
          color: var(--muted);
          padding: 14px 0;
        }}
        .progress-panel {{
          display: none;
          margin-top: 18px;
        }}
        .progress-status {{
          color: var(--muted);
          line-height: 1.5;
        }}
        .progress-log {{
          max-height: 240px;
          overflow-y: auto;
          margin-top: 12px;
        }}
        .preview-list {{
          max-height: 280px;
          overflow-y: auto;
        }}
        .preview-actions {{
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          margin-bottom: 10px;
        }}
        .tiny-button {{
          border: 1px solid #9ac7c3;
          background: #ffffff;
          color: #134e4a;
          border-radius: 999px;
          padding: 6px 12px;
          font-size: 0.88rem;
          font-weight: 700;
          cursor: pointer;
          box-shadow: none;
        }}
        .preview-item {{
          display: flex;
          align-items: flex-start;
          gap: 12px;
          width: 100%;
        }}
        .preview-checkbox {{
          margin-top: 4px;
          width: 18px;
          height: 18px;
        }}
        .preview-selection {{
          margin-top: 8px;
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
        }}
        .preview-selection-option {{
          display: inline-flex;
          align-items: center;
          gap: 6px;
          color: var(--muted);
          font-size: 0.88rem;
          font-weight: 700;
        }}
        .preview-selection-option input {{
          width: 16px;
          height: 16px;
        }}
      </style>
    </head>
    <body>
      <main>
        <section class="hero">
          <div class="eyebrow">Private Weekly Runner</div>
          <h1>Quantum Newsletter Control Room</h1>
          <p class="lead">Generate the latest weekly newsletter from the sheet, then open any archived HTML file directly from this page.</p>
          <form class="controls" id="generate-form">
            <div class="field-row">
              <label class="field">
                <span>Start date</span>
                <input type="date" name="start_date" value="{default_start}" required />
              </label>
              <label class="field">
                <span>End date</span>
                <input type="date" name="end_date" value="{today}" required />
              </label>
            </div>
            {key_fields}
            <div class="actions">
              <button type="button" class="secondary-button" id="preview-button">Preview Stories</button>
              <button type="submit" id="generate-button">Generate Latest Newsletter</button>
            </div>
            <div class="note">Story parsing timeout: {story_timeout}s per story. Slow stories fall back automatically.</div>
          </form>
          <div id="banner-root"></div>
          <section class="archive-panel" id="preview-panel">
            <div class="archive-header">
              <h2>Story Preview</h2>
              <div class="note" id="preview-meta">Select dates and click preview.</div>
            </div>
            <div class="preview-actions">
              <button type="button" class="tiny-button" id="select-all-button">Select all</button>
              <button type="button" class="tiny-button" id="unselect-all-button">Unselect all</button>
            </div>
            <div id="preview-list" class="preview-list">
              <div class="empty-state">No preview loaded yet.</div>
            </div>
          </section>
          <section class="archive-panel progress-panel" id="progress-panel">
            <div class="archive-header">
              <h2>Generation Progress</h2>
              <div class="note" id="progress-counter">0 parsed</div>
            </div>
            <div class="progress-status" id="progress-status">Waiting to start...</div>
            <ul class="archive-list progress-log" id="progress-log"></ul>
          </section>
          {storage_message}
        </section>

        <section class="archive-panel">
          <div class="archive-header">
            <h2>Server Archive</h2>
            <div class="note">{count} archived</div>
          </div>
          {archives_html}
        </section>

        <section class="archive-panel">
          <div class="archive-header">
            <h2>Browser Archive</h2>
            <div class="note">Stored on this device</div>
          </div>
          <div id="browser-archives"></div>
        </section>
      </main>
      <script>
        const bannerRoot = document.getElementById('banner-root');
        const form = document.getElementById('generate-form');
        const button = document.getElementById('generate-button');
        const previewButton = document.getElementById('preview-button');
        const selectAllButton = document.getElementById('select-all-button');
        const unselectAllButton = document.getElementById('unselect-all-button');
        const browserArchives = document.getElementById('browser-archives');
        const progressPanel = document.getElementById('progress-panel');
        const progressCounter = document.getElementById('progress-counter');
        const progressStatus = document.getElementById('progress-status');
        const progressLog = document.getElementById('progress-log');
        const previewList = document.getElementById('preview-list');
        const previewMeta = document.getElementById('preview-meta');
        const browserArchiveKey = 'websummarizer-browser-archive';
        let previewStartDate = '';
        let previewEndDate = '';

        function readBrowserArchive() {{
          try {{
            return JSON.parse(localStorage.getItem(browserArchiveKey) || '[]');
          }} catch (error) {{
            return [];
          }}
        }}

        function writeBrowserArchive(items) {{
          localStorage.setItem(browserArchiveKey, JSON.stringify(items));
        }}

        function showBanner(message, tone) {{
          const className = tone === 'error' ? 'banner error' : 'banner success';
          bannerRoot.innerHTML = '<div class="' + className + '">' + message + '</div>';
        }}

        function escapeHtml(raw) {{
          return String(raw || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
        }}

        function resetProgress() {{
          progressPanel.style.display = 'block';
          progressCounter.textContent = '0 parsed';
          progressStatus.textContent = 'Starting generation...';
          progressLog.innerHTML = '';
        }}

        function updateProgress(payload) {{
          const total = Number(payload.total || 0);
          const scanned = Number(payload.scanned || 0);
          const parsed = Number(payload.parsed || 0);
          const skipped = Number(payload.skipped || 0);
          const fallback = Number(payload.fallback || 0);

          if (total > 0) {{
            progressCounter.textContent = parsed + '/' + total + ' parsed · ' + scanned + '/' + total + ' scanned · ' + skipped + ' skipped · ' + fallback + ' fallback';
          }} else {{
            progressCounter.textContent = parsed + ' parsed';
          }}
          progressStatus.textContent = payload.message || 'Working...';

          const line = (payload.message || '').trim();
          if (!line) return;
          const meta = payload.url ? escapeHtml(payload.url) : '';
          const item = document.createElement('li');
          item.className = 'archive-item';
          item.innerHTML = '<div><div class="archive-name">' + escapeHtml(line) + '</div>' +
            (meta ? '<div class="archive-meta">' + meta + '</div>' : '') +
          '</div>';
          progressLog.prepend(item);
          while (progressLog.children.length > 8) {{
            progressLog.removeChild(progressLog.lastChild);
          }}
        }}

        function openBrowserArchive(index) {{
          const items = readBrowserArchive();
          const item = items[index];
          if (!item) return;
          const blob = new Blob([item.html], {{ type: 'text/html' }});
          const url = URL.createObjectURL(blob);
          window.open(url, '_blank', 'noopener');
          setTimeout(() => URL.revokeObjectURL(url), 60000);
        }}

        function openBrowserArchiveEditor(index) {{
          const items = readBrowserArchive();
          const item = items[index];
          if (!item) return;
          const token = 'websummarizer-editor-' + Date.now() + '-' + Math.random().toString(16).slice(2);
          localStorage.setItem(token, JSON.stringify({{
            filename: item.filename,
            generatedAt: item.generatedAt,
            html: item.html,
          }}));
          window.open('/archive/edit?source=browser&token=' + encodeURIComponent(token), '_blank', 'noopener');
        }}

        function renderBrowserArchives() {{
          const items = readBrowserArchive();
          if (!items.length) {{
            browserArchives.innerHTML = '<div class="empty-state">No browser-saved newsletters yet.</div>';
            return;
          }}

          browserArchives.innerHTML = '<ul class="archive-list">' + items.map((item, index) => {{
            return '<li class="archive-item">' +
              '<div>' +
                '<div class="archive-name">' + item.filename + '</div>' +
                '<div class="archive-meta">' + item.generatedAt + '</div>' +
              '</div>' +
              '<div class="archive-actions">' +
                '<button class="browser-open" type="button" onclick="openBrowserArchive(' + index + ')">Open</button>' +
                '<button class="browser-open" type="button" onclick="openBrowserArchiveEditor(' + index + ')">Edit</button>' +
              '</div>' +
            '</li>';
          }}).join('') + '</ul>';
        }}

        window.openBrowserArchive = openBrowserArchive;
        window.openBrowserArchiveEditor = openBrowserArchiveEditor;
        renderBrowserArchives();

        function storyKey(story) {{
          return String(story.index) + '::' + String(story.url || '');
        }}

        function getSelectionMode(primaryChecked, additionalChecked) {{
          if (additionalChecked) {{
            return 'additional';
          }}
          if (primaryChecked) {{
            return 'primary';
          }}
          return 'skip';
        }}

        function getSelectionMap() {{
          const map = {{}};
          previewList.querySelectorAll('.preview-selection').forEach((row) => {{
            const key = String(row.dataset.storyKey || '');
            const primary = row.querySelector('.preview-story-checkbox-primary');
            const additional = row.querySelector('.preview-story-checkbox-additional');
            if (!key || !primary || !additional) {{
              return;
            }}
            map[key] = getSelectionMode(Boolean(primary.checked), Boolean(additional.checked));
          }});
          return map;
        }}

        function refreshPreviewMeta() {{
          const rows = previewList.querySelectorAll('.preview-selection');
          if (!rows.length) {{
            return;
          }}
          const total = rows.length;
          let selectedPrimary = 0;
          let selectedAdditional = 0;
          rows.forEach((row) => {{
            const primary = row.querySelector('.preview-story-checkbox-primary');
            const additional = row.querySelector('.preview-story-checkbox-additional');
            const mode = getSelectionMode(Boolean(primary && primary.checked), Boolean(additional && additional.checked));
            if (mode === 'primary') {{
              selectedPrimary += 1;
            }} else if (mode === 'additional') {{
              selectedAdditional += 1;
            }}
          }});
          const skipped = total - selectedPrimary - selectedAdditional;
          previewMeta.textContent =
            selectedPrimary + ' in newsletter · ' +
            selectedAdditional + ' additional links · ' +
            skipped + ' skipped' +
            ' between ' + previewStartDate + ' and ' + previewEndDate;
        }}

        function handleSelectionToggle(event) {{
          const target = event.target;
          if (!target) {{
            return;
          }}
          const row = target.closest('.preview-selection');
          if (!row) {{
            return;
          }}
          const primary = row.querySelector('.preview-story-checkbox-primary');
          const additional = row.querySelector('.preview-story-checkbox-additional');
          if (!primary || !additional) {{
            return;
          }}

          if (target.classList.contains('preview-story-checkbox-additional') && additional.checked) {{
            primary.checked = false;
          }}
          if (target.classList.contains('preview-story-checkbox-primary') && primary.checked) {{
            additional.checked = false;
          }}
          refreshPreviewMeta();
        }}

        function setAllSelections(checked) {{
          const rows = previewList.querySelectorAll('.preview-selection');
          rows.forEach((row) => {{
            const primary = row.querySelector('.preview-story-checkbox-primary');
            const additional = row.querySelector('.preview-story-checkbox-additional');
            if (primary) {{
              primary.checked = checked;
            }}
            if (additional) {{
              additional.checked = false;
            }}
          }});
          refreshPreviewMeta();
        }}

        function getStorySelections(stories) {{
          const selectionMap = getSelectionMap();
          const selectedStories = [];
          const additionalStories = [];
          (Array.isArray(stories) ? stories : []).forEach((story) => {{
            const mode = String(selectionMap[storyKey(story)] || 'skip');
            if (mode === 'primary') {{
              selectedStories.push(story);
            }} else if (mode === 'additional') {{
              additionalStories.push(story);
            }}
          }});
          return {{
            selectedStories: selectedStories,
            additionalStories: additionalStories,
          }};
        }}

        function renderPreview(stories, startDate, endDate, selectionMap) {{
          const list = Array.isArray(stories) ? stories : [];
          previewStartDate = startDate || '';
          previewEndDate = endDate || '';
          const safeSelection = selectionMap || {{}};
          previewMeta.textContent = list.length + ' stories between ' + previewStartDate + ' and ' + previewEndDate;
          if (!list.length) {{
            previewList.innerHTML = '<div class="empty-state">No stories found for this range.</div>';
            return;
          }}

          previewList.innerHTML = '<ul class="archive-list">' + list.map((story, idx) => {{
            const title = escapeHtml(story.title_seed || '(No headline)');
            const url = escapeHtml(story.url || '');
            const tag = escapeHtml(story.tag || 'untagged');
            const published = escapeHtml(story.published_at || 'unknown date');
            const key = storyKey(story);
            const savedMode = Object.prototype.hasOwnProperty.call(safeSelection, key) ? safeSelection[key] : 'primary';
            const mode = savedMode === true ? 'primary' : (savedMode === false ? 'skip' : String(savedMode || 'primary'));
            const primaryCheckedAttr = mode === 'primary' ? ' checked' : '';
            const additionalCheckedAttr = mode === 'additional' ? ' checked' : '';
            return '<li class="archive-item">' +
              '<div class="preview-item">' +
                '<div>' +
                  '<div class="archive-name">' + (idx + 1) + '. ' + title + '</div>' +
                  '<div class="archive-meta">' + published + ' · ' + tag + '</div>' +
                  '<div class="archive-meta">' + url + '</div>' +
                  '<div class="preview-selection" data-story-key="' + escapeHtml(key) + '">' +
                    '<label class="preview-selection-option">' +
                      '<input type="checkbox" class="preview-story-checkbox-primary"' + primaryCheckedAttr + ' />' +
                      '<span>Add to newsletter</span>' +
                    '</label>' +
                    '<label class="preview-selection-option">' +
                      '<input type="checkbox" class="preview-story-checkbox-additional"' + additionalCheckedAttr + ' />' +
                      '<span>Additional links</span>' +
                    '</label>' +
                  '</div>' +
                '</div>' +
              '</div>' +
            '</li>';
          }}).join('') + '</ul>';

          previewList.querySelectorAll('.preview-story-checkbox-primary, .preview-story-checkbox-additional').forEach((checkbox) => {{
            checkbox.addEventListener('change', handleSelectionToggle);
          }});
          refreshPreviewMeta();
        }}

        async function parseJsonResponse(response) {{
          const text = await response.text();
          if (!text) {{
            return {{}};
          }}
          try {{
            return JSON.parse(text);
          }} catch (error) {{
            throw new Error(text || ('Request failed with status ' + response.status));
          }}
        }}

        async function postForm(url, formData) {{
          const response = await fetch(url, {{
            method: 'POST',
            body: new URLSearchParams(formData),
          }});
          const payload = await parseJsonResponse(response);
          if (!response.ok || payload.ok === false) {{
            throw new Error(payload.error || ('Request failed with status ' + response.status));
          }}
          return payload;
        }}

        async function postJson(url, payload) {{
          const response = await fetch(url, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify(payload),
          }});
          const body = await parseJsonResponse(response);
          if (!response.ok || body.ok === false) {{
            throw new Error(body.error || ('Request failed with status ' + response.status));
          }}
          return body;
        }}

        previewButton.addEventListener('click', async () => {{
          previewButton.disabled = true;
          const previousLabel = previewButton.textContent;
          previewButton.textContent = 'Loading preview...';
          bannerRoot.innerHTML = '';

          try {{
            const formData = new FormData(form);
            const payload = await postForm('/stories/preview', formData);
            renderPreview(payload.stories, payload.start_date, payload.end_date);
            showBanner('Preview loaded: ' + payload.total + ' stories.', 'success');
          }} catch (error) {{
            showBanner(error.message, 'error');
          }} finally {{
            previewButton.disabled = false;
            previewButton.textContent = previousLabel;
          }}
        }});

        selectAllButton.addEventListener('click', () => {{
          setAllSelections(true);
        }});

        unselectAllButton.addEventListener('click', () => {{
          setAllSelections(false);
        }});

        form.addEventListener('submit', async (event) => {{
          event.preventDefault();
          button.disabled = true;
          previewButton.disabled = true;
          selectAllButton.disabled = true;
          unselectAllButton.disabled = true;
          button.textContent = 'Generating...';
          bannerRoot.innerHTML = '';
          resetProgress();

          try {{
            const formData = new FormData(form);
            const apiKey = String(formData.get('openai_api_key') || '').trim();
            const previousSelection = getSelectionMap();
            const startPayload = await postForm('/generate/start', formData);
            const stories = Array.isArray(startPayload.stories) ? startPayload.stories : [];
            renderPreview(stories, startPayload.start_date, startPayload.end_date, previousSelection);
            const selections = getStorySelections(stories);
            const selectedStories = selections.selectedStories;
            const additionalStories = selections.additionalStories;
            const total = selectedStories.length;
            if (!total) {{
              throw new Error('Select at least one story with "Add to newsletter" before generating.');
            }}
            const parsedStories = [];
            let skippedCount = 0;
            let fallbackCount = 0;

            updateProgress({{
              message: 'Loaded ' + stories.length + ' stories from sheet; selected ' + total + ' for newsletter and ' + additionalStories.length + ' as additional links.',
              total: total,
              scanned: 0,
              parsed: 0,
              skipped: 0,
              fallback: 0,
            }});

            for (let i = 0; i < selectedStories.length; i += 1) {{
              const story = selectedStories[i];
              const scanned = i + 1;
              updateProgress({{
                message: 'Parsing story ' + scanned + ' of ' + total,
                total: total,
                scanned: scanned,
                parsed: parsedStories.length,
                skipped: skippedCount,
                fallback: fallbackCount,
                url: story.url || '',
              }});

              const storyPayload = await postJson('/generate/story', {{
                index: story.index,
                url: story.url,
                title_seed: story.title_seed,
                tag: story.tag,
                openai_api_key: apiKey,
              }});

              if (storyPayload.skipped) {{
                skippedCount += 1;
                updateProgress({{
                  message: 'Skipped story ' + scanned + ': ' + (storyPayload.reason || 'No summary generated'),
                  total: total,
                  scanned: scanned,
                  parsed: parsedStories.length,
                  skipped: skippedCount,
                  fallback: fallbackCount,
                  url: story.url || '',
                }});
              }} else if (storyPayload.story) {{
                parsedStories.push(storyPayload.story);
                if (storyPayload.fallback) {{
                  fallbackCount += 1;
                }}
                updateProgress({{
                  message: storyPayload.fallback
                    ? ('Used fallback for story ' + scanned + ' of ' + total)
                    : ('Parsed story ' + scanned + ' of ' + total),
                  total: total,
                  scanned: scanned,
                  parsed: parsedStories.length,
                  skipped: skippedCount,
                  fallback: fallbackCount,
                  url: story.url || '',
                }});
              }}
            }}

            updateProgress({{
              message: 'Generating headline and final HTML...',
              total: total,
              scanned: total,
              parsed: parsedStories.length,
              skipped: skippedCount,
              fallback: fallbackCount,
            }});

            const payload = await postJson('/generate/finalize', {{
              stories: parsedStories,
              additional_links: additionalStories,
              openai_api_key: apiKey,
            }});

            if (payload.stored === 'browser') {{
              const items = readBrowserArchive();
              items.unshift({{
                filename: payload.filename,
                generatedAt: payload.generated_at,
                headline: payload.headline,
                html: payload.html,
              }});
              writeBrowserArchive(items.slice(0, 25));
              renderBrowserArchives();
              showBanner('Generated ' + payload.filename + ' and saved it in this browser.', 'success');
            }} else {{
              showBanner('Generated ' + payload.filename + ' and archived it on the server.', 'success');
              window.location.reload();
            }}
          }} catch (error) {{
            showBanner(error.message, 'error');
          }} finally {{
            button.disabled = false;
            previewButton.disabled = false;
            selectAllButton.disabled = false;
            unselectAllButton.disabled = false;
            button.textContent = 'Generate Latest Newsletter';
          }}
        }});
      </script>
    </body>
    </html>
    """.format(
        storage_message=storage_message,
        archives_html=archives_html,
        count=len(archives),
        days=DEFAULT_LOOKBACK_DAYS,
        default_start=default_start,
        today=today,
        story_timeout=STORY_PARSE_TIMEOUT_SECONDS,
        key_fields=key_fields,
    )


@app.route("/generate/start", methods=["POST"])
@require_auth
def generate_start():
    try:
        prepare_runtime_env(
            api_key=request.form.get("openai_api_key", "").strip(),
            require_openai=False,
        )
        start_dt, end_dt = resolve_date_range(
            request.form.get("start_date", ""),
            request.form.get("end_date", ""),
            days=DEFAULT_LOOKBACK_DAYS,
        )
        spreadsheet_handler = load_spreadsheet_data(
            DEFAULT_LOOKBACK_DAYS,
            start_date=start_dt,
            end_date=end_dt,
        )
        stories = []
        for index, url in enumerate(spreadsheet_handler.urls):
            stories.append(
                {
                    "index": index,
                    "url": url,
                    "title_seed": spreadsheet_handler.titles[index],
                    "tag": spreadsheet_handler.tags[index],
                    "published_at": spreadsheet_handler.published_at[index],
                }
            )
        return jsonify(
            {
                "ok": True,
                "total": len(stories),
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": end_dt.strftime("%Y-%m-%d"),
                "stories": stories,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/stories/preview", methods=["POST"])
@require_auth
def stories_preview():
    try:
        prepare_runtime_env(
            api_key=request.form.get("openai_api_key", "").strip(),
            require_openai=False,
        )
        start_dt, end_dt = resolve_date_range(
            request.form.get("start_date", ""),
            request.form.get("end_date", ""),
            days=DEFAULT_LOOKBACK_DAYS,
        )
        spreadsheet_handler = load_spreadsheet_data(
            DEFAULT_LOOKBACK_DAYS,
            start_date=start_dt,
            end_date=end_dt,
        )
        stories = []
        for index, url in enumerate(spreadsheet_handler.urls):
            stories.append(
                {
                    "index": index,
                    "url": url,
                    "title_seed": spreadsheet_handler.titles[index],
                    "tag": spreadsheet_handler.tags[index],
                    "published_at": spreadsheet_handler.published_at[index],
                }
            )
        return jsonify(
            {
                "ok": True,
                "total": len(stories),
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": end_dt.strftime("%Y-%m-%d"),
                "stories": stories,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/generate/story", methods=["POST"])
@require_auth
def generate_story():
    payload = request.get_json(silent=True) or {}
    index = int(payload.get("index", 0))
    url = (payload.get("url") or "").strip()
    title_seed = payload.get("title_seed") or ""
    tag = payload.get("tag") or ""

    try:
        prepare_runtime_env(api_key=(payload.get("openai_api_key") or "").strip())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    if not url:
        return jsonify({"ok": False, "error": "Story URL is missing."}), 400

    story, failure_reason = process_story_with_timeout(
        index, url, title_seed, tag, STORY_PARSE_TIMEOUT_SECONDS
    )

    if not story:
        fallback_story = build_fallback_story(
            index,
            url,
            title_seed,
            tag,
            reason=failure_reason or "No summary generated",
        )
        return jsonify(
            {
                "ok": True,
                "skipped": False,
                "fallback": True,
                "index": index,
                "url": url,
                "reason": failure_reason or "No summary generated",
                "story": fallback_story,
            }
        )

    return jsonify(
        {
            "ok": True,
            "skipped": False,
            "fallback": False,
            "index": index,
            "story": story,
        }
    )


@app.route("/generate/finalize", methods=["POST"])
@require_auth
def generate_finalize():
    payload = request.get_json(silent=True) or {}
    try:
        prepare_runtime_env(api_key=(payload.get("openai_api_key") or "").strip())
        raw_stories = payload.get("stories") or []
        raw_additional_links = payload.get("additional_links") or []
        if not isinstance(raw_stories, list):
            return jsonify({"ok": False, "error": "Invalid stories payload."}), 400
        if not isinstance(raw_additional_links, list):
            return jsonify({"ok": False, "error": "Invalid additional links payload."}), 400

        stories = [story for story in raw_stories if isinstance(story, dict)]
        additional_links = [story for story in raw_additional_links if isinstance(story, dict)]
        newsletter = finalize_newsletter(stories, additional_links=additional_links)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = "newsletter_%s.html" % timestamp
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if blob_archive_configured():
            archived = save_newsletter_html(filename, newsletter["html"])
            return jsonify(
                {
                    "ok": True,
                    "stored": "server",
                    "filename": filename,
                    "pathname": archived["pathname"],
                    "headline": newsletter["headline"],
                    "generated_at": generated_at,
                }
            )

        return jsonify(
            {
                "ok": True,
                "stored": "browser",
                "filename": filename,
                "headline": newsletter["headline"],
                "generated_at": generated_at,
                "html": newsletter["html"],
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/generate", methods=["POST"])
@require_auth
def generate():
    return jsonify(
        {
            "ok": False,
            "error": "Deprecated endpoint. Refresh this page and retry generation.",
        }
    ), 410


@app.route("/archive/edit")
@require_auth
def edit_archive():
    pathname = (request.args.get("pathname") or "").strip()
    source = (request.args.get("source") or "server").strip().lower()
    token = (request.args.get("token") or "").strip()
    filename = (request.args.get("filename") or "").strip() or "newsletter_edited.html"
    initial_html = ""

    if pathname:
        if not is_valid_archive_path(pathname):
            return Response("Not found", status=404)
        result = load_newsletter_html(pathname)
        if result is None:
            return Response("Not found", status=404)
        html_content, _content_type = result
        initial_html = decode_html_payload(html_content)
        filename = pathname.split("/")[-1]
        source = "server"
    elif source == "browser":
        source = "browser"
    else:
        return Response("Not found", status=404)

    payload = {
        "source": source,
        "token": token,
        "filename": filename,
        "initial_html": initial_html,
        "requires_api_key": not bool(os.getenv("OPENAI_API_KEY")),
        "model": OPENAI_EDIT_MODEL,
    }
    return Response(render_archive_editor_html(payload), content_type="text/html; charset=utf-8")


@app.route("/archive/edit/apply", methods=["POST"])
@require_auth
def apply_archive_edit():
    payload = request.get_json(silent=True) or {}
    html_content = payload.get("html") or ""
    instruction = (payload.get("instruction") or "").strip()
    provided_api_key = (payload.get("openai_api_key") or "").strip()
    effective_api_key = provided_api_key or (os.getenv("OPENAI_API_KEY") or "").strip()

    if not isinstance(html_content, str):
        return jsonify({"ok": False, "error": "Invalid HTML payload."}), 400
    if not instruction:
        return jsonify({"ok": False, "error": "Edit instruction is required."}), 400

    if not effective_api_key:
        return jsonify({"ok": False, "error": "OpenAI API key is required."}), 400

    raw_messages = payload.get("messages") or []
    history = []
    if isinstance(raw_messages, list):
        for item in raw_messages[-OPENAI_EDIT_HISTORY_LIMIT:]:
            if not isinstance(item, dict):
                continue
            role = (item.get("role") or "").strip()
            content = (item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            history.append({"role": role, "content": content[:1200]})

    model_html, data_uri_tokens, data_uri_count = redact_inline_data_uris(html_content)
    asset_note = ""
    if data_uri_count:
        asset_note = (
            "\n\nNote: inline base64 asset payloads were replaced with placeholder tokens like "
            "__INLINE_DATA_URI_00001__ to keep this request within token limits. Preserve these tokens."
        )

    prompt = (
        "User request:\n%s\n\n"
        "Current HTML:\n%s\n\n"
        "Return the complete updated HTML document."
    ) % (instruction, model_html + asset_note)

    system_prompt = (
        "You are an HTML newsletter editor.\n"
        "Apply the user request directly to the provided HTML.\n"
        "Keep the output as a complete, valid HTML document.\n"
        "Never omit sections unless the user asked for removal.\n"
        "Respond as strict JSON with exactly two keys:\n"
        "updated_html: full edited HTML string\n"
        "assistant_message: concise summary of what was changed."
    )

    try:
        client = OpenAI(api_key=effective_api_key)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        try:
            completion = client.chat.completions.create(
                model=OPENAI_EDIT_MODEL,
                messages=messages,
            )
            content = ""
            if getattr(completion, "choices", None):
                content = completion.choices[0].message.content or ""
            updated_html_model, assistant_message = parse_html_edit_response(content, model_html)
        except Exception as exc:
            if not is_request_too_large_error(exc):
                raise

            body_segments = extract_body_segments(model_html)
            if not body_segments:
                raise

            prefix, body_html, suffix = body_segments
            if len(body_html) > OPENAI_EDIT_MAX_BODY_CHARS:
                return jsonify(
                    {
                        "ok": False,
                        "error": (
                            "Edit request is still too large after optimization. "
                            "Try editing a smaller section or removing oversized embedded content first."
                        ),
                    }
                ), 413

            body_system_prompt = (
                "You are an HTML newsletter editor.\n"
                "Apply the user request only to the provided BODY fragment.\n"
                "Return valid HTML fragment for the body contents only (no <html>, <head>, or <body> wrapper).\n"
                "Preserve placeholder tokens like __INLINE_DATA_URI_00001__ unless user requested removing that element.\n"
                "Respond as strict JSON with exactly two keys:\n"
                "updated_body_html: full edited body fragment\n"
                "assistant_message: concise summary of what changed."
            )
            body_prompt = (
                "User request:\n%s\n\n"
                "Current BODY HTML fragment:\n%s\n\n"
                "Return only the updated BODY fragment."
            ) % (instruction, body_html + asset_note)

            body_messages = [
                {"role": "system", "content": body_system_prompt},
                {"role": "user", "content": body_prompt},
            ]
            completion = client.chat.completions.create(
                model=OPENAI_EDIT_MODEL,
                messages=body_messages,
            )
            content = ""
            if getattr(completion, "choices", None):
                content = completion.choices[0].message.content or ""
            updated_body, assistant_message = parse_body_edit_response(content, body_html)
            updated_html_model = prefix + updated_body + suffix

        updated_html = restore_inline_data_uris(updated_html_model, data_uri_tokens)
        if not updated_html.strip():
            return jsonify({"ok": False, "error": "The model returned an empty HTML document."}), 500
        if data_uri_count:
            assistant_message = (
                (assistant_message or "Applied your requested edits.")
                + " Preserved %d inline asset payload(s)." % data_uri_count
            )
        return jsonify(
            {
                "ok": True,
                "updated_html": updated_html,
                "assistant_message": assistant_message or "Applied your requested edits.",
                "model": OPENAI_EDIT_MODEL,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": "Failed to apply AI edit: %s" % exc}), 500


@app.route("/archive")
@require_auth
def view_archive():
    pathname = request.args.get("pathname", "")
    if not pathname or not is_valid_archive_path(pathname):
        return Response("Not found", status=404)

    result = load_newsletter_html(pathname)
    if result is None:
        return Response("Not found", status=404)

    html_content, content_type = result
    return Response(html_content, content_type=content_type or "text/html; charset=utf-8")
