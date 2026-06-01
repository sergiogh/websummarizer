import json
import re
from typing import Dict, Optional

from qa_checks import validate_summary_claims
from prompt_loader import get_prompt
from summary_generator import SummaryGenerator


_FALLBACK_REPAIRABLE_FLAGS = {
    "summary_numbers_not_in_source",
    "summary_entities_not_in_source",
    "summary_low_source_overlap",
    "summary_claims_not_supported",
}

_FALLBACK_BLOCKING_FLAGS = {
    "insufficient_source_content",
    "source_title_mismatch",
}

_BOILERPLATE_HINTS = (
    "accept cookies",
    "advertisement",
    "all rights reserved",
    "cookie policy",
    "follow us",
    "latest posts",
    "newsletter",
    "privacy policy",
    "read more",
    "related article",
    "share this",
    "sign up",
    "subscribe",
    "terms of use",
)

_QUANTUM_HINTS = {
    "algorithm",
    "computing",
    "cryptography",
    "error",
    "fidelity",
    "hardware",
    "ion",
    "logical",
    "photon",
    "processor",
    "qubit",
    "quantum",
    "research",
    "superconducting",
}


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


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _tokens(text: str):
    return set(re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower()))


def _split_source_sentences(text: str):
    cleaned = _normalize_spaces(text)
    if not cleaned:
        return []
    protected = cleaned
    placeholder = "<DOT>"
    for token in ("Dr.", "Prof.", "Inc.", "Ltd.", "U.S.", "U.K.", "e.g.", "i.e."):
        protected = re.sub(
            re.escape(token),
            token.replace(".", placeholder),
            protected,
            flags=re.IGNORECASE,
        )
    parts = re.split(r"(?<=[.!?])\s+", protected)
    return [_normalize_spaces(part.replace(placeholder, ".")) for part in parts if _normalize_spaces(part)]


def _is_source_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if len(sentence) < 45 or len(sentence) > 420:
        return False
    if lowered.count("|") >= 2:
        return False
    if any(hint in lowered for hint in _BOILERPLATE_HINTS):
        return False
    if len(_tokens(sentence)) < 8:
        return False
    return True


def build_extractive_fallback_summary(
    intended_title: str,
    article_text: str,
    metadata: Optional[Dict[str, object]] = None,
    max_words: int = 115,
) -> Dict[str, object]:
    """Build a conservative summary by reusing article sentences verbatim."""
    metadata = metadata or {}
    source_text = article_text or ""
    if metadata.get("extraction_status") == "insufficient_content" or len(source_text) < 300:
        return {"summary": "", "evidence": [], "status": "insufficient_content"}

    title_context = " ".join(
        str(value or "")
        for value in (
            intended_title,
            metadata.get("h1"),
            metadata.get("og_title"),
            metadata.get("twitter_title"),
            metadata.get("html_title"),
        )
    )
    title_tokens = {token for token in _tokens(title_context) if len(token) > 2}
    sentences = [sentence for sentence in _split_source_sentences(source_text) if _is_source_sentence(sentence)]
    if not sentences:
        return {"summary": "", "evidence": [], "status": "insufficient_content"}

    scored = []
    for index, sentence in enumerate(sentences[:80]):
        sentence_tokens = _tokens(sentence)
        score = 0
        score += min(6, len(sentence_tokens & title_tokens) * 2)
        score += min(4, len(sentence_tokens & _QUANTUM_HINTS))
        if re.search(r"\d", sentence):
            score += 2
        if index < 5:
            score += 2
        elif index < 12:
            score += 1
        scored.append((score, index, sentence))

    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:3]
    selected = sorted(selected, key=lambda item: item[1])

    summary_parts = []
    word_count = 0
    for _, _, sentence in selected:
        words = sentence.split()
        if not words:
            continue
        if word_count and word_count + len(words) > max_words:
            continue
        if not word_count and len(words) > max_words:
            sentence = " ".join(words[:max_words]).rstrip(" ,;:")
            if sentence and sentence[-1] not in ".!?":
                sentence += "."
            words = sentence.split()
        summary_parts.append(sentence)
        word_count += len(words)
        if word_count >= max_words:
            break

    summary = _normalize_spaces(" ".join(summary_parts))
    if not summary:
        return {"summary": "", "evidence": [], "status": "insufficient_content"}

    evidence = [sentence for _, _, sentence in selected[:4]]
    return {
        "summary": summary,
        "evidence": evidence,
        "status": "extractive_fallback",
    }


def build_safe_summary_fallback(
    intended_title: str,
    article_text: str,
    metadata: Optional[Dict[str, object]] = None,
    flags=None,
) -> Optional[Dict[str, object]]:
    flags = set(flags or [])
    if flags & _FALLBACK_BLOCKING_FLAGS:
        return None
    if flags and not (flags & _FALLBACK_REPAIRABLE_FLAGS):
        return None

    fallback = build_extractive_fallback_summary(intended_title, article_text, metadata)
    summary = fallback.get("summary", "")
    if not summary:
        return None

    claim_result = validate_summary_claims(summary, article_text or "")
    if not claim_result["passed"]:
        return None

    fallback["claim_check"] = claim_result
    fallback["flags_resolved"] = sorted(flags & _FALLBACK_REPAIRABLE_FLAGS)
    fallback["remaining_flags"] = sorted(flags - _FALLBACK_REPAIRABLE_FLAGS - {"full_page_fallback_extraction"})
    return fallback


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
