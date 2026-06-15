import re
from typing import Dict, List, Sequence, Set

from title_utils import strip_source_from_title


_STOPWORDS = {
    "a",
    "about",
    "after",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "new",
    "of",
    "on",
    "or",
    "over",
    "says",
    "the",
    "to",
    "using",
    "with",
}


def _find_repeated_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9']+", text.lower())
    repeats = []
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            repeats.append(tokens[i])
    return repeats


def _collapse_repeats(text: str) -> str:
    return re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)


def qa_title_summary(title: str, summary: str) -> Dict[str, object]:
    flags = []
    fixed_title = title or ""
    fixed_summary = summary or ""

    if re.search(r"https?://|www\.", fixed_title, flags=re.IGNORECASE):
        flags.append("title_contains_url")
        fixed_title = re.sub(r"https?://\\S+|www\\.\\S+", "", fixed_title).strip()

    if re.search(r"https?://|www\.", fixed_summary, flags=re.IGNORECASE):
        flags.append("summary_contains_url")
        fixed_summary = re.sub(r"https?://\\S+|www\\.\\S+", "", fixed_summary).strip()

    stripped = strip_source_from_title(fixed_title)
    if stripped != fixed_title:
        flags.append("title_contains_source")
        fixed_title = stripped

    repeats = _find_repeated_tokens(fixed_title)
    if repeats:
        flags.append("title_repeated_tokens")
        fixed_title = _collapse_repeats(fixed_title)

    return {
        "flags": flags,
        "title_original": title,
        "summary_original": summary,
        "title_fixed": fixed_title,
        "summary_fixed": fixed_summary
    }


def _tokens(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower())
        if len(token) > 2 and token not in _STOPWORDS
    ]


def _token_set(text: str) -> Set[str]:
    return set(_tokens(text))


def _numbers(text: str) -> Set[str]:
    return set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text or ""))


def _proper_noun_phrases(text: str) -> Set[str]:
    phrases = set()
    pattern = r"\b(?:[A-Z][A-Za-z0-9&.-]+(?:\s+|$)){1,5}"
    for match in re.finditer(pattern, text or ""):
        phrase = re.sub(r"\s+", " ", match.group(0)).strip()
        if len(phrase) >= 3 and phrase.lower() not in _STOPWORDS:
            phrases.add(phrase.lower())
    return phrases


def _best_overlap_score(query: str, candidates: Sequence[str]) -> float:
    query_tokens = _token_set(query)
    if not query_tokens:
        return 0.0
    best = 0.0
    for candidate in candidates:
        candidate_tokens = _token_set(candidate)
        if not candidate_tokens:
            continue
        score = len(query_tokens & candidate_tokens) / len(query_tokens)
        best = max(best, score)
    return best


def _find_evidence_sentences(summary: str, source_text: str, limit: int = 3) -> List[str]:
    source_sentences = re.split(r"(?<=[.!?])\s+", source_text or "")
    summary_tokens = _token_set(summary)
    scored = []
    for sentence in source_sentences:
        sentence_tokens = _token_set(sentence)
        if not sentence_tokens:
            continue
        overlap = len(summary_tokens & sentence_tokens)
        if overlap:
            scored.append((overlap, sentence.strip()))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    return [sentence for _, sentence in scored[:limit]]


def validate_story_grounding(
    intended_title: str,
    summary: str,
    source_text: str,
    metadata: Dict[str, object] = None,
) -> Dict[str, object]:
    """Check that the summary and downloaded source match the selected story.

    This is intentionally heuristic. It catches common failure modes before the
    newsletter claims that a story has been verified: wrong redirect target,
    flat-page extraction dominated by related links, and summaries containing
    numbers or named actors absent from the source.
    """
    metadata = metadata or {}
    flags: List[str] = []
    title_candidates = [
        str(metadata.get("h1", "") or ""),
        str(metadata.get("og_title", "") or ""),
        str(metadata.get("twitter_title", "") or ""),
        str(metadata.get("html_title", "") or ""),
    ]
    title_overlap = _best_overlap_score(intended_title, title_candidates)
    body_overlap = _best_overlap_score(intended_title, [source_text])

    if metadata.get("extraction_status") == "insufficient_content" or len(source_text or "") < 300:
        flags.append("insufficient_source_content")
    if metadata.get("extraction_status") == "fallback_full_page":
        flags.append("full_page_fallback_extraction")
    if intended_title and title_candidates and title_overlap < 0.25 and body_overlap < 0.25:
        flags.append("source_title_mismatch")

    source_numbers = _numbers(source_text)
    summary_numbers = _numbers(summary)
    missing_numbers = sorted(summary_numbers - source_numbers)
    if missing_numbers:
        flags.append("summary_numbers_not_in_source")

    source_entities = _proper_noun_phrases(source_text)
    title_entities = _proper_noun_phrases(intended_title)
    summary_entities = _proper_noun_phrases(summary)
    missing_title_entities = sorted(entity for entity in title_entities if entity not in source_entities)
    missing_summary_entities = sorted(entity for entity in summary_entities if entity not in source_entities)
    if missing_title_entities and title_overlap < 0.4 and body_overlap < 0.4:
        flags.append("title_entities_not_in_source")
    if missing_summary_entities:
        flags.append("summary_entities_not_in_source")

    summary_source_overlap = _best_overlap_score(summary, [source_text])
    if summary and source_text and summary_source_overlap < 0.18:
        flags.append("summary_low_source_overlap")

    hard_fail_flags = {
        "insufficient_source_content",
        "source_title_mismatch",
        "summary_numbers_not_in_source",
        "title_entities_not_in_source",
    }
    passed = not any(flag in hard_fail_flags for flag in flags)

    return {
        "passed": passed,
        "flags": flags,
        "title_overlap": round(title_overlap, 3),
        "body_overlap": round(body_overlap, 3),
        "summary_source_overlap": round(summary_source_overlap, 3),
        "missing_numbers": missing_numbers,
        "missing_title_entities": missing_title_entities,
        "missing_summary_entities": missing_summary_entities,
        "evidence": _find_evidence_sentences(summary, source_text),
        "metadata_titles": [candidate for candidate in title_candidates if candidate],
    }


def validate_summary_claims(summary: str, source_text: str) -> Dict[str, object]:
    claims = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", summary or "")
        if sentence.strip()
    ]
    unsupported = []
    evidence = {}
    for claim in claims:
        claim_tokens = _token_set(claim)
        if not claim_tokens:
            continue
        best_evidence = _find_evidence_sentences(claim, source_text, limit=1)
        best_sentence = best_evidence[0] if best_evidence else ""
        source_tokens = _token_set(best_sentence)
        overlap = len(claim_tokens & source_tokens) / max(1, len(claim_tokens))
        full_source_tokens = _token_set(source_text)
        full_source_overlap = len(claim_tokens & full_source_tokens) / max(1, len(claim_tokens))
        missing_numbers = sorted(_numbers(claim) - _numbers(source_text))
        if missing_numbers or (overlap < 0.35 and full_source_overlap < 0.2):
            unsupported.append(
                {
                    "claim": claim,
                    "overlap": round(overlap, 3),
                    "full_source_overlap": round(full_source_overlap, 3),
                    "missing_numbers": missing_numbers,
                    "best_evidence": best_sentence,
                }
            )
        elif best_sentence:
            evidence[claim] = best_sentence

    return {
        "passed": not unsupported,
        "claims_checked": len(claims),
        "unsupported_claims": unsupported,
        "evidence": evidence,
    }


def validate_aggregate_grounding(text: str, stories: Sequence[Dict[str, object]]) -> Dict[str, object]:
    source_text = " ".join(
        " ".join(
            str(value or "")
            for value in (
                story.get("title"),
                story.get("summary"),
                story.get("url"),
            )
        )
        for story in stories
    )
    result = validate_summary_claims(text, source_text)

    source_entities = _proper_noun_phrases(source_text)
    aggregate_entities = _proper_noun_phrases(text)
    missing_entities = sorted(entity for entity in aggregate_entities if entity not in source_entities)
    source_numbers = _numbers(source_text)
    missing_numbers = sorted(_numbers(text) - source_numbers)

    flags = []
    if missing_entities:
        flags.append("aggregate_entities_not_in_passed_stories")
    if missing_numbers:
        flags.append("aggregate_numbers_not_in_passed_stories")
    if not result["passed"]:
        flags.append("aggregate_unsupported_claims")

    return {
        "passed": not flags,
        "flags": flags,
        "missing_entities": missing_entities,
        "missing_numbers": missing_numbers,
        "claims": result,
    }
