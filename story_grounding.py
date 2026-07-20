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
    "facebook linkedin",
    "find winning stocks",
    "for investors",
    "follow us",
    "latest posts",
    "log in",
    "newsletter",
    "next post",
    "previous article",
    "previous post",
    "print email",
    "privacy policy",
    "read full article",
    "read more",
    "related article",
    "share this",
    "sign up",
    "sign in",
    "shutterstock",
    "subscribe",
    "tags ",
    "terms of use",
    "weekly round-up",
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

_SOURCE_METADATA_HINTS = (
    "accesswire",
    "associated press",
    "business wire",
    "ein presswire",
    "globenewswire",
    "marketwatch",
    "morningstar",
    "pr newswire",
    "press release",
    "seeking alpha",
    "simply wall st",
    "yahoo finance",
)

_SUMMARY_JUNK_HINTS = (
    "find winning stocks",
    "read full article",
    "ticker",
)

_PROMOTIONAL_SUMMARY_PHRASES = (
    "best-in-class",
    "game-changing",
    "groundbreaking",
    "industry-leading",
    "pleased to announce",
    "proud to announce",
    "revolutionary",
    "transformative",
    "unprecedented opportunity",
    "world-class",
    "world-leading",
)

_DATE_OR_READTIME_RE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|"
    r"august|september|october|november|december)\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|"
    r"august|september|october|november|december)\s+\d{1,2},\s+20\d{2}\b|"
    r"\b\d+\s+min\s+read\b|"
    r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\s*(?:edt|est|cst|cdt|mst|mdt|pst|pdt|gmt|utc)?\b",
    flags=re.IGNORECASE,
)

_TICKER_RUN_RE = re.compile(
    r"\b(?:[A-Z]{1,6}(?:\.[A-Z]{1,4})?\s+){2,}[A-Z]{1,6}(?:\.[A-Z]{1,4})?\b"
)

_SOURCE_PARENTHESES_RE = re.compile(
    r"\s*\((?:source|via|from|reported by)\s*:\s*[^)]*(?:press release|wire|newswire|yahoo|simply wall)[^)]*\)",
    flags=re.IGNORECASE,
)

_PRESS_RELEASE_DATELINE_RE = re.compile(
    r"^\s*(?:[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,5},\s+){1,3}"
    r"(?:[A-Z][a-z]+\.?\s+\d{1,2},\s+20\d{2}|[A-Z]{2,})"
    r"(?:\s*/\s*[^/]{3,60}\s*/)?\s*(?:--|[-\u2013\u2014])\s*"
)

_LEADING_LABEL_RE = re.compile(
    r"^\s*(?:summary|story|article|source|press release|snippet)\s*[:\-]\s*",
    flags=re.IGNORECASE,
)

_SUBSTANTIVE_VERB_RE = re.compile(
    r"\b(?:announced|backs|built|chose|closed|collaborated|completed|created|debuted|decided|"
    r"declined|demonstrated|developed|expanded|explained|expects|formed|introduced|launched|"
    r"led|partnered|published|raised|released|reported|said|secured|selected|signed|supports|"
    r"targets|tested|unveiled|uses|will|would|aims|includes|included|plans|is|are|has|have)\b",
    flags=re.IGNORECASE,
)


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


def _split_summary_sentences(text: str):
    return _split_source_sentences(text)


def _has_ticker_run(text: str) -> bool:
    match = _TICKER_RUN_RE.search(text or "")
    return bool(match and ("." in match.group(0) or len(match.group(0).split()) >= 3))


def _is_metadata_sentence(sentence: str, intended_title: str = "") -> bool:
    lowered = (sentence or "").lower()
    has_source = any(hint in lowered for hint in _SOURCE_METADATA_HINTS)
    has_junk = any(hint in lowered for hint in _SUMMARY_JUNK_HINTS)
    has_market_tickers = _has_ticker_run(sentence)
    has_date_or_readtime = bool(_DATE_OR_READTIME_RE.search(sentence or ""))
    title_tokens = {token for token in _tokens(intended_title) if len(token) > 2}
    sentence_tokens = _tokens(sentence)
    title_overlap = bool(title_tokens) and len(title_tokens & sentence_tokens) / max(len(title_tokens), 1) >= 0.45

    if has_junk or has_market_tickers:
        return True
    if has_source and (has_date_or_readtime or title_overlap):
        return True
    if has_source and len(sentence.split()) <= 18:
        return True
    return False


def _strip_market_metadata(text: str) -> str:
    cleaned = _SOURCE_PARENTHESES_RE.sub("", text or "")
    cleaned = _LEADING_LABEL_RE.sub("", cleaned)
    cleaned = _PRESS_RELEASE_DATELINE_RE.sub("", cleaned)
    cleaned = _TICKER_RUN_RE.sub("", cleaned)
    cleaned = re.sub(r"\b\d+\s+min\s+read\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\bFind winning stocks in any market cycle\.?",
        ". ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _normalize_spaces(cleaned)


def _is_complete_story_sentence(sentence: str) -> bool:
    cleaned = _normalize_spaces(sentence)
    if not cleaned:
        return False
    if cleaned[-1] not in ".!?":
        return False
    if len(_tokens(cleaned)) < 7:
        return False
    if not _SUBSTANTIVE_VERB_RE.search(cleaned):
        return False
    return True


def _has_story_shape(text: str) -> bool:
    sentences = _split_summary_sentences(text)
    complete = [sentence for sentence in sentences if _is_complete_story_sentence(sentence)]
    if not complete:
        return False
    if len(_tokens(" ".join(complete))) < 18:
        return False
    return True


def sanitize_story_summary_text(
    summary: str,
    intended_title: str = "",
) -> Dict[str, object]:
    """Remove source metadata and market cruft from a generated story summary."""
    original = _normalize_spaces(summary)
    if not original:
        return {"summary": "", "flags": []}

    flags = []
    cleaned = _strip_market_metadata(original)
    if cleaned != original:
        flags.append("summary_market_metadata_removed")

    sentences = _split_summary_sentences(cleaned)
    if sentences:
        kept = []
        removed = False
        for sentence in sentences:
            if _is_metadata_sentence(sentence, intended_title):
                removed = True
                continue
            kept.append(sentence)
        if removed:
            flags.append("summary_metadata_sentence_removed")
            cleaned = _normalize_spaces(" ".join(kept))

    story_sentences = [
        sentence
        for sentence in _split_summary_sentences(cleaned)
        if _is_complete_story_sentence(sentence)
    ]
    if story_sentences and _normalize_spaces(" ".join(story_sentences)) != cleaned:
        flags.append("summary_fragments_removed")
        cleaned = _normalize_spaces(" ".join(story_sentences))

    cleaned = cleaned.strip(" \t\r\n\"'\u201c\u201d\u2018\u2019")
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."

    return {"summary": cleaned, "flags": flags}


def _summary_needs_repair(summary: str) -> bool:
    cleaned = _normalize_spaces(summary)
    if not cleaned:
        return True
    if len(_tokens(cleaned)) < 14:
        return True
    if _is_metadata_sentence(cleaned):
        return True
    if not _has_story_shape(cleaned):
        return True
    if cleaned.count(".") == 0 and len(cleaned.split()) > 35:
        return True
    return False


def _longest_shared_token_run(left: str, right: str) -> int:
    left_tokens = re.findall(r"[a-z0-9][a-z0-9'-]*", (left or "").lower())
    right_tokens = re.findall(r"[a-z0-9][a-z0-9'-]*", (right or "").lower())
    if not left_tokens or not right_tokens:
        return 0

    previous = [0] * (len(right_tokens) + 1)
    longest = 0
    for left_token in left_tokens:
        current = [0]
        for right_index, right_token in enumerate(right_tokens, start=1):
            if left_token == right_token:
                run = previous[right_index - 1] + 1
                current.append(run)
                longest = max(longest, run)
            else:
                current.append(0)
        previous = current
    return longest


def evaluate_human_summary_style(summary: str, source_text: str = "") -> Dict[str, object]:
    """Flag summaries that are long, promotional, or too close to source prose."""
    cleaned = _normalize_spaces(summary)
    words = cleaned.split()
    lowered = cleaned.lower()
    flags = []
    if cleaned and len(words) < 70:
        flags.append("summary_too_short")
    if len(words) > 145:
        flags.append("summary_too_long")
    if any(phrase in lowered for phrase in _PROMOTIONAL_SUMMARY_PHRASES):
        flags.append("summary_promotional_language")
    if re.search(r"\b(?:we|our)\s+(?:are|believe|expect|have|will)\b", cleaned, flags=re.IGNORECASE):
        flags.append("summary_press_release_voice")

    longest_shared_run = _longest_shared_token_run(cleaned, source_text)
    if longest_shared_run >= 18:
        flags.append("summary_excessive_verbatim_overlap")

    return {
        "passed": not flags,
        "flags": flags,
        "word_count": len(words),
        "longest_shared_token_run": longest_shared_run,
    }


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
    if _is_metadata_sentence(sentence):
        return False
    if len(_tokens(sentence)) < 8:
        return False
    return True


def build_extractive_fallback_summary(
    intended_title: str,
    article_text: str,
    metadata: Optional[Dict[str, object]] = None,
    max_words: int = 220,
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

    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:5]
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
    summary = sanitize_story_summary_text(summary, intended_title).get("summary", "")
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

    remaining_flags = sorted(flags - _FALLBACK_REPAIRABLE_FLAGS - {"full_page_fallback_extraction"})
    if remaining_flags:
        return None

    fallback["claim_check"] = claim_result
    fallback["flags_resolved"] = sorted(flags & _FALLBACK_REPAIRABLE_FLAGS)
    fallback["remaining_flags"] = remaining_flags
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
    cleanup = sanitize_story_summary_text(summary, intended_title)
    summary = cleanup["summary"]
    cleanup_flags = cleanup["flags"]
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
    elif status == "ok" and _summary_needs_repair(summary):
        fallback = build_extractive_fallback_summary(intended_title, article_text, metadata)
        if fallback.get("summary"):
            summary = fallback["summary"]
            evidence = fallback.get("evidence", evidence)
            cleanup_flags = list(dict.fromkeys(cleanup_flags + ["summary_repaired_with_extractive_fallback"]))

    style_result = evaluate_human_summary_style(summary, article_text)
    style_rewritten = False
    if status == "ok" and not style_result["passed"]:
        rewrite_payload = json.dumps(
            {
                "INTENDED_TITLE": intended_title or "",
                "PAGE_TITLE": (metadata or {}).get("html_title", ""),
                "PAGE_HEADLINE": (metadata or {}).get("h1", ""),
                "ARTICLE_TEXT": (article_text or "")[:12000],
                "DRAFT_SUMMARY": summary,
                "STYLE_ISSUES": style_result["flags"],
            },
            ensure_ascii=False,
            indent=2,
        )
        rewrite = SummaryGenerator("").generate_json_summary(
            get_prompt("summary.story.human_rewrite"),
            rewrite_payload,
        )
        rewritten_summary = sanitize_story_summary_text(
            str(rewrite.get("summary", "") or ""),
            intended_title,
        ).get("summary", "")
        rewritten_style = evaluate_human_summary_style(rewritten_summary, article_text)
        if rewritten_summary and rewritten_style["passed"]:
            summary = rewritten_summary
            style_result = rewritten_style
            style_rewritten = True
            rewrite_evidence = rewrite.get("evidence", [])
            if isinstance(rewrite_evidence, list):
                evidence = rewrite_evidence

    return {
        "status": status,
        "summary": summary,
        "matched_title": str(result.get("matched_title", "") or "").strip(),
        "evidence": [str(item).strip() for item in evidence if str(item).strip()][:4],
        "confidence": max(0.0, min(1.0, confidence)),
        "cleanup_flags": cleanup_flags,
        "style": style_result,
        "style_rewritten": style_rewritten,
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
