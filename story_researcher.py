import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse


DEFAULT_RESEARCH_LIMIT = 20
MAX_RESEARCH_WINDOW_DAYS = 7
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


def normalize_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path or "")
    query = parsed.query
    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def title_signature(title: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
    stopwords = {
        "a",
        "an",
        "and",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
    }
    return " ".join(token for token in tokens if token not in stopwords)[:120]


def existing_story_index(stories: Iterable[Dict[str, object]]) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for position, story in enumerate(stories or []):
        url = normalize_url(str(story.get("url", "") or ""))
        title = title_signature(str(story.get("title_seed") or story.get("title") or ""))
        label = str(story.get("title_seed") or story.get("title") or url or f"story {position + 1}")
        if url:
            index[f"url:{url}"] = label
        if title:
            index[f"title:{title}"] = label
    return index


def is_duplicate_candidate(candidate: Dict[str, object], existing_index: Dict[str, str]) -> Optional[str]:
    url = normalize_url(str(candidate.get("url", "") or ""))
    title = title_signature(str(candidate.get("title") or candidate.get("title_seed") or ""))
    if url and f"url:{url}" in existing_index:
        return existing_index[f"url:{url}"]
    if title and f"title:{title}" in existing_index:
        return existing_index[f"title:{title}"]
    return None


def _coerce_confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def parse_candidate_date(value: str):
    cleaned = (value or "").strip()
    if not cleaned:
        return None

    iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", cleaned)
    if iso_match:
        year, month, day = iso_match.groups()
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            return None

    slash_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", cleaned)
    if slash_match:
        month, day, year = slash_match.groups()
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            return None

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    return None


def _date_window_bounds(start_date, end_date):
    def _as_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return parse_candidate_date(value)
        return None

    return _as_date(start_date), _as_date(end_date)


def clamp_research_window(start_date, end_date, max_days: int = MAX_RESEARCH_WINDOW_DAYS):
    """Return an inclusive research window capped at the final ``max_days`` days."""
    start_bound, end_bound = _date_window_bounds(start_date, end_date)
    if start_bound is None or end_bound is None:
        raise ValueError("Research requires valid start and end dates.")
    if start_bound > end_bound:
        raise ValueError("Research start date must be before end date.")

    safe_days = max(1, min(int(max_days), MAX_RESEARCH_WINDOW_DAYS))
    earliest_allowed = end_bound - timedelta(days=safe_days - 1)
    bounded_start = max(start_bound, earliest_allowed)

    if isinstance(end_date, datetime):
        bounded_end = end_date
    else:
        bounded_end = datetime.combine(end_bound, datetime.max.time())
    if isinstance(start_date, datetime):
        bounded_start_dt = start_date.replace(
            year=bounded_start.year,
            month=bounded_start.month,
            day=bounded_start.day,
        )
    else:
        bounded_start_dt = datetime.combine(bounded_start, datetime.min.time())
    return bounded_start_dt, bounded_end


def is_date_in_window(published_at: str, start_date=None, end_date=None) -> bool:
    if start_date is None and end_date is None:
        return True

    candidate_date = parse_candidate_date(published_at)
    start_bound, end_bound = _date_window_bounds(start_date, end_date)
    if candidate_date is None or start_bound is None or end_bound is None:
        return False
    return start_bound <= candidate_date <= end_bound


def normalize_research_candidates(
    raw_candidates,
    existing_stories: Iterable[Dict[str, object]] = (),
    limit: int = DEFAULT_RESEARCH_LIMIT,
    start_date=None,
    end_date=None,
) -> List[Dict[str, object]]:
    if not isinstance(raw_candidates, list):
        return []

    existing_index = existing_story_index(existing_stories)
    seen_urls = set()
    candidates: List[Dict[str, object]] = []

    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        url = normalize_url(str(raw.get("url") or raw.get("source_url") or "").strip())
        title = str(raw.get("title") or raw.get("headline") or "").strip()
        if not url or not title:
            continue
        if url in seen_urls:
            continue

        tag = str(raw.get("tag") or raw.get("category") or "research").strip().lower()
        if tag not in {"research", "industry", "investment", "policy", "ecosystem", "other"}:
            tag = "research"
        if tag == "investment":
            tag = "industry"

        duplicate_of = is_duplicate_candidate({"url": url, "title": title}, existing_index)
        publisher = str(raw.get("publisher") or raw.get("source") or "").strip()
        published_at = str(raw.get("published_at") or raw.get("date") or "").strip()
        parsed_published_date = parse_candidate_date(published_at)
        if not is_date_in_window(published_at, start_date, end_date):
            continue
        rationale = str(raw.get("rationale") or raw.get("why_it_matters") or "").strip()
        citation_urls = raw.get("citation_urls") or raw.get("citations") or [url]
        if not isinstance(citation_urls, list):
            citation_urls = [url]

        candidates.append(
            {
                "id": f"research_{len(candidates)}",
                "index": 10000 + len(candidates),
                "url": url,
                "title": title,
                "title_seed": title,
                "publisher": publisher,
                "published_at": parsed_published_date.isoformat() if parsed_published_date else published_at,
                "tag": tag,
                "rationale": rationale,
                "source": "openai_web_search",
                "citation_urls": [normalize_url(str(item)) for item in citation_urls if str(item).strip()][:5],
                "duplicate_of": duplicate_of,
                "confidence": _coerce_confidence(raw.get("confidence", 0.5)),
            }
        )
        seen_urls.add(url)
        if len(candidates) >= limit:
            break

    return candidates


def _strip_json_fence(text: str) -> str:
    cleaned = (text or "").strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", cleaned, flags=re.IGNORECASE)
    return match.group(1).strip() if match else cleaned


def parse_research_response(text: str) -> List[Dict[str, object]]:
    cleaned = _strip_json_fence(text)
    if not cleaned:
        return []
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    if isinstance(parsed, dict):
        candidates = parsed.get("candidates") or parsed.get("stories") or []
        return candidates if isinstance(candidates, list) else []
    if isinstance(parsed, list):
        return parsed
    return []


def _format_existing_stories(stories: Iterable[Dict[str, object]]) -> str:
    lines = []
    for story in stories or []:
        title = str(story.get("title_seed") or story.get("title") or "").strip()
        url = str(story.get("url") or "").strip()
        if title or url:
            lines.append(f"- {title} | {url}")
    return "\n".join(lines[:80])


def build_research_prompt(start_date: datetime, end_date: datetime, existing_stories, limit: int) -> str:
    existing_block = _format_existing_stories(existing_stories)
    return (
        "Research quantum computing news stories and scientific articles published in the date window below. "
        "Find candidates that could enrich a technical newsletter, prioritizing primary sources, papers, "
        "university or company announcements, government publications, standards/security items, and reputable reporting.\n\n"
        f"DATE_WINDOW_START: {start_date.strftime('%Y-%m-%d')}\n"
        f"DATE_WINDOW_END: {end_date.strftime('%Y-%m-%d')}\n"
        "DATE_WINDOW_RULE: This inclusive window is at most seven calendar days. Never return older stories.\n"
        f"MAX_CANDIDATES: {limit}\n\n"
        "Already covered spreadsheet stories:\n"
        f"{existing_block or '(none)'}\n\n"
        "Return only valid JSON with this shape:\n"
        "{\n"
        '  "candidates": [\n'
        "    {\n"
        '      "title": "story headline",\n'
        '      "url": "primary source or best article URL",\n'
        '      "publisher": "source name",\n'
        '      "published_at": "YYYY-MM-DD if known",\n'
        '      "tag": "research|industry|policy|ecosystem|other",\n'
        '      "rationale": "one sentence explaining why this is relevant",\n'
        '      "citation_urls": ["supporting URL"],\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Exclude duplicates of the already covered stories where possible. Do not write newsletter summaries. "
        "Do not include a candidate without a URL. Do not include a candidate unless its publication date is known "
        "and falls inside DATE_WINDOW_START through DATE_WINDOW_END, inclusive. Treat the publication date as a "
        "hard filter, not a relevance hint."
    )


def extract_response_text(payload: Dict[str, object]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts = []
    for output_item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []) if isinstance(output_item.get("content"), list) else []:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


class OpenAIWebSearchResearchProvider:
    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_RESEARCH_MODEL", "gpt-5.1")

    def search(self, start_date: datetime, end_date: datetime, existing_stories, limit: int = DEFAULT_RESEARCH_LIMIT):
        if not self.api_key:
            raise RuntimeError("OpenAI API key is required for story research.")

        request_body = {
            "model": self.model,
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
            "input": build_research_prompt(start_date, end_date, existing_stories, limit),
        }
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OpenAI research request failed: {body or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI research request failed: {exc.reason}") from exc

        raw_candidates = parse_research_response(extract_response_text(payload))
        return normalize_research_candidates(
            raw_candidates,
            existing_stories,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )


def research_quantum_stories(
    start_date: datetime,
    end_date: datetime,
    existing_stories,
    limit: int = DEFAULT_RESEARCH_LIMIT,
    provider=None,
):
    start_date, end_date = clamp_research_window(start_date, end_date)
    active_provider = provider or OpenAIWebSearchResearchProvider()
    return active_provider.search(start_date, end_date, existing_stories, limit=limit)
