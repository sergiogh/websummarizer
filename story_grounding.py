import json
from typing import Dict, Optional

from prompt_loader import get_prompt
from summary_generator import SummaryGenerator


def build_summary_payload(
    intended_title: str,
    url: str,
    article_text: str,
    metadata: Optional[Dict[str, object]] = None,
) -> str:
    metadata = metadata or {}
    payload = {
        "INTENDED_TITLE": intended_title or "",
        "SOURCE_URL": url or "",
        "CANONICAL_URL": metadata.get("canonical_url", ""),
        "PAGE_TITLE": metadata.get("html_title", ""),
        "PAGE_HEADLINE": metadata.get("h1") or metadata.get("og_title") or metadata.get("twitter_title", ""),
        "EXTRACTION_STATUS": metadata.get("extraction_status", ""),
        "ARTICLE_TEXT": (article_text or "")[:12000],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def failure_summary(status: str, flags=None) -> str:
    flags = flags or []
    if status == "source_mismatch" or "source_title_mismatch" in flags:
        return "Unable to generate a source-grounded summary: the downloaded source does not clearly match the selected story title."
    if status == "insufficient_content" or "insufficient_source_content" in flags:
        return "Unable to generate a source-grounded summary: the downloaded source text is insufficient or mostly boilerplate."
    if flags:
        return "Unable to generate a source-grounded summary: source validation flagged this story for manual review."
    return "Unable to generate a source-grounded summary from the provided source text."


def is_story_passed(story: Dict[str, object]) -> bool:
    flags = story.get("qa_flags") or []
    grounding = story.get("grounding") or {}
    if flags:
        return False
    if isinstance(grounding, dict) and grounding.get("passed") is False:
        return False
    return True


def filter_passed_stories(results):
    return [story for story in results if is_story_passed(story)]


def generate_grounded_summary(
    intended_title: str,
    article_text: str,
    url: str = "",
    metadata: Optional[Dict[str, object]] = None,
    prompt_key: str = "summary.story.grounded",
) -> Dict[str, object]:
    prompt = get_prompt(prompt_key)
    payload = build_summary_payload(intended_title, url, article_text, metadata)
    generator = SummaryGenerator("")
    result = generator.generate_json_summary(prompt, payload)

    status = str(result.get("status", "") or "").strip().lower()
    if status not in {"ok", "source_mismatch", "insufficient_content"}:
        status = "insufficient_content"

    summary = str(result.get("summary", "") or "").strip()
    evidence = result.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []

    confidence = result.get("confidence", 0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    if status != "ok" and not summary:
        summary = failure_summary(status)

    return {
        "status": status,
        "summary": summary,
        "matched_title": str(result.get("matched_title", "") or "").strip(),
        "evidence": [str(item).strip() for item in evidence if str(item).strip()][:4],
        "confidence": max(0.0, min(1.0, confidence)),
    }


def generate_grounded_title(
    intended_title: str,
    article_text: str,
    url: str = "",
    metadata: Optional[Dict[str, object]] = None,
    prompt_key: str = "title.story.grounded",
) -> Dict[str, object]:
    prompt = get_prompt(prompt_key)
    payload = build_summary_payload(intended_title, url, article_text, metadata)
    generator = SummaryGenerator("")
    result = generator.generate_json_summary(prompt, payload)

    status = str(result.get("status", "") or "").strip().lower()
    if status not in {"ok", "source_mismatch", "insufficient_content"}:
        status = "insufficient_content"

    evidence = result.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []

    confidence = result.get("confidence", 0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "status": status,
        "title": str(result.get("title", "") or "").strip(),
        "matched_title": str(result.get("matched_title", "") or "").strip(),
        "evidence": [str(item).strip() for item in evidence if str(item).strip()][:3],
        "confidence": max(0.0, min(1.0, confidence)),
    }
