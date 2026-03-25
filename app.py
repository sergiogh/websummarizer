import base64
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

    rewritten_title = generate_title(summary, url, is_paper)
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


def finalize_newsletter(results):
    if not results:
        raise RuntimeError("No newsletter stories were generated.")

    curated = curate_stories(results)
    primary_results = curated["primary"]
    overflow_results = curated["overflow"]

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
                  <a class="archive-link" href="/archive?pathname={pathname}">Open</a>
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
              '<button class="browser-open" type="button" onclick="openBrowserArchive(' + index + ')">Open</button>' +
            '</li>';
          }}).join('') + '</ul>';
        }}

        window.openBrowserArchive = openBrowserArchive;
        renderBrowserArchives();

        function storyKey(story) {{
          return String(story.index) + '::' + String(story.url || '');
        }}

        function getSelectionMap() {{
          const map = {{}};
          previewList.querySelectorAll('.preview-story-checkbox').forEach((checkbox) => {{
            map[String(checkbox.dataset.storyKey || '')] = checkbox.checked;
          }});
          return map;
        }}

        function refreshPreviewMeta() {{
          const checkboxes = previewList.querySelectorAll('.preview-story-checkbox');
          if (!checkboxes.length) {{
            return;
          }}
          const total = checkboxes.length;
          let selected = 0;
          checkboxes.forEach((checkbox) => {{
            if (checkbox.checked) {{
              selected += 1;
            }}
          }});
          previewMeta.textContent = selected + '/' + total + ' selected between ' + previewStartDate + ' and ' + previewEndDate;
        }}

        function setAllSelections(checked) {{
          const checkboxes = previewList.querySelectorAll('.preview-story-checkbox');
          checkboxes.forEach((checkbox) => {{
            checkbox.checked = checked;
          }});
          refreshPreviewMeta();
        }}

        function getSelectedStories(stories) {{
          const selectedKeys = new Set();
          previewList.querySelectorAll('.preview-story-checkbox').forEach((checkbox) => {{
            if (checkbox.checked) {{
              selectedKeys.add(String(checkbox.dataset.storyKey || ''));
            }}
          }});
          return (Array.isArray(stories) ? stories : []).filter((story) => selectedKeys.has(storyKey(story)));
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
            const checked = Object.prototype.hasOwnProperty.call(safeSelection, key) ? Boolean(safeSelection[key]) : true;
            const checkedAttr = checked ? ' checked' : '';
            return '<li class="archive-item">' +
              '<div class="preview-item">' +
                '<input type="checkbox" class="preview-checkbox preview-story-checkbox" data-story-key="' + escapeHtml(key) + '"' + checkedAttr + ' />' +
                '<div>' +
                  '<div class="archive-name">' + (idx + 1) + '. ' + title + '</div>' +
                  '<div class="archive-meta">' + published + ' · ' + tag + '</div>' +
                  '<div class="archive-meta">' + url + '</div>' +
                '</div>' +
              '</div>' +
            '</li>';
          }}).join('') + '</ul>';

          previewList.querySelectorAll('.preview-story-checkbox').forEach((checkbox) => {{
            checkbox.addEventListener('change', refreshPreviewMeta);
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
            const selectedStories = getSelectedStories(stories);
            const total = selectedStories.length;
            if (!total) {{
              throw new Error('Select at least one story in preview before generating.');
            }}
            const parsedStories = [];
            let skippedCount = 0;
            let fallbackCount = 0;

            updateProgress({{
              message: 'Loaded ' + stories.length + ' stories from sheet; selected ' + total + ' for parsing.',
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
        if not isinstance(raw_stories, list):
            return jsonify({"ok": False, "error": "Invalid stories payload."}), 400

        stories = [story for story in raw_stories if isinstance(story, dict)]
        newsletter = finalize_newsletter(stories)
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
