import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


STORY_BUCKET_RESEARCH = "research"
STORY_BUCKET_INDUSTRY_INVESTMENT = "industry_investment"
STORY_BUCKET_POLICY_SECURITY = "policy_security"
STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM = "infrastructure_ecosystem"
STORY_BUCKET_OTHER = "other"

STORY_BUCKET_SEQUENCE = (
    STORY_BUCKET_RESEARCH,
    STORY_BUCKET_INDUSTRY_INVESTMENT,
    STORY_BUCKET_POLICY_SECURITY,
    STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM,
    STORY_BUCKET_OTHER,
)

STORY_BUCKET_LABELS = {
    STORY_BUCKET_RESEARCH: "Research & Papers",
    STORY_BUCKET_INDUSTRY_INVESTMENT: "Industry & Investment",
    STORY_BUCKET_POLICY_SECURITY: "Policy & Security",
    STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM: "Infrastructure & Ecosystem",
    STORY_BUCKET_OTHER: "Other",
}

STORY_BUCKET_DESCRIPTIONS = {
    STORY_BUCKET_RESEARCH: "New findings and reproducible technical results.",
    STORY_BUCKET_INDUSTRY_INVESTMENT: "Commercial launches, partnerships, funding, and market moves.",
    STORY_BUCKET_POLICY_SECURITY: "Government policy, standards, regulation, and security posture.",
    STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM: "Platforms, tooling, education, and ecosystem readiness.",
    STORY_BUCKET_OTHER: "Relevant developments that do not fit the main channels.",
}

_BUCKET_PRIORITY = {
    STORY_BUCKET_RESEARCH: 0,
    STORY_BUCKET_INDUSTRY_INVESTMENT: 1,
    STORY_BUCKET_POLICY_SECURITY: 2,
    STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM: 3,
    STORY_BUCKET_OTHER: 4,
}

_RESEARCH_HINTS = (
    "research",
    "researchers",
    "scientific",
    "paper",
    "study",
    "preprint",
    "benchmark",
    "published",
    "publication",
    "experiment",
    "experimental",
    "findings",
    "scientists",
    "university",
    "universities",
    "institute",
    "institutes",
    "laboratory",
    "laboratories",
    "professor",
)

_RESEARCH_URL_HINTS = (
    "/abs/",
    "/pdf/",
    "/article/",
    "/articles/",
    "/content/",
    "/research",
    "/paper",
    ".pdf",
)

_INDUSTRY_HINTS = (
    "investment",
    "investments",
    "investor",
    "investors",
    "funding",
    "raised",
    "raises",
    "raise",
    "series a",
    "series b",
    "series c",
    "series d",
    "seed round",
    "venture",
    "commercial",
    "commercialization",
    "commercialisation",
    "procurement",
    "deploy",
    "deployment",
    "enterprise",
    "launch",
    "launches",
    "platform",
    "product",
    "partnership",
    "partnerships",
    "acquires",
    "acquisition",
    "merger",
    "contract",
    "contracts",
    "market",
    "stock",
    "listed",
)

_POLICY_SECURITY_HINTS = (
    "policy",
    "government",
    "ministry",
    "department",
    "national",
    "federal",
    "regulator",
    "regulation",
    "regulatory",
    "watchdog",
    "antitrust",
    "defense",
    "defence",
    "military",
    "security",
    "zero trust",
    "post-quantum",
    "cryptography",
    "encryption",
    "nist",
    "standards",
    "compliance",
)

_INFRASTRUCTURE_ECOSYSTEM_HINTS = (
    "roadmap",
    "workforce",
    "training",
    "education",
    "academy",
    "curriculum",
    "hub",
    "testbed",
    "data center",
    "supercomputing",
    "open access",
    "open source",
    "ecosystem",
    "consortium",
    "framework",
    "integration",
    "orchestration",
)

_HIGH_TRUST_DOMAINS = (
    "arxiv.org",
    "nature.com",
    "science.org",
    "gov",
    "edu",
    "ac.",
    "ibm.com",
    "nvidia.com",
    "darpa.mil",
)

_LOW_TRUST_DOMAINS = (
    "share.google",
    "linkedin.com/safety/go",
)

_PRESS_RELEASE_HINTS = (
    "prnewswire",
    "businesswire",
    "globenewswire",
    "einpresswire",
)

_METRIC_HINTS = (
    "qubit",
    "qubits",
    "fidelity",
    "error",
    "percent",
    "million",
    "billion",
    "times",
    "fold",
    "hours",
    "minutes",
    "seconds",
    "dollar",
    "euro",
    "pound",
)

_IMPACT_HINTS = (
    "first",
    "record",
    "breakthrough",
    "largest",
    "fastest",
    "funding",
    "investment",
    "deploy",
    "launched",
    "commercial",
    "benchmark",
    "demonstrated",
)

_WHAT_LABEL = "What happened:"
_KEY_LABEL = "Key detail:"
_WHY_LABEL = "Why this matters:"

_FALLBACK_KEY_DETAIL = "No quantitative metric disclosed in source"
_FALLBACK_WHY = "No impact statement can be derived from the available source text"

_DEFAULT_PRIMARY_LIMIT = 20
_DEFAULT_OVERFLOW_LIMIT = 8

_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "new",
    "using",
    "uses",
    "this",
    "that",
    "says",
    "said",
    "announces",
    "announced",
    "unveils",
    "unveiled",
}

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}


def _count_matches(text: str, hints: Sequence[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for hint in hints if hint in lowered)


def _has_academic_domain(domain: str) -> bool:
    lowered = (domain or "").lower()
    return (
        lowered.endswith(".edu")
        or ".ac." in lowered
        or "university" in lowered
        or "institute" in lowered
        or "laboratory" in lowered
    )


def _has_government_domain(domain: str) -> bool:
    lowered = (domain or "").lower()
    return lowered.endswith(".gov") or lowered.endswith(".mil")


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _sentence_to_clause(text: str) -> str:
    cleaned = _normalize_spaces(text).rstrip(".!?")
    if not cleaned:
        return ""
    return cleaned[0].lower() + cleaned[1:] if len(cleaned) > 1 else cleaned.lower()


def _strip_matter_prefix(text: str) -> str:
    cleaned = _normalize_spaces(text)
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = re.sub(
            r"^(?:why\s+(?:this|it)\s+matters\s*:?\s*)?(?:this|it)\s+matters\s+because\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
    return cleaned


def _is_failure_summary(text: str) -> bool:
    return _normalize_spaces(text).lower().startswith(
        "unable to generate a source-grounded summary"
    )


def _sentence_from_clause(text: str) -> str:
    cleaned = _strip_matter_prefix(text).rstrip(".!?")
    if not cleaned:
        return ""
    if cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return _ensure_sentence(cleaned, "")


def _extract_labeled(text: str, label: str, next_labels: Sequence[str]) -> str:
    if not text:
        return ""
    escaped_label = re.escape(label)
    next_union = "|".join(re.escape(item) for item in next_labels)
    pattern = rf"{escaped_label}\s*(.*?)(?=(?:{next_union})|$)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _normalize_spaces(match.group(1))


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    cleaned = _normalize_spaces(text)

    placeholder = "<DOT>"
    abbreviations = (
        "vs.",
        "e.g.",
        "i.e.",
        "etc.",
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "prof.",
        "sr.",
        "jr.",
    )
    protected = cleaned
    for token in abbreviations:
        protected = re.sub(
            re.escape(token),
            token.replace(".", placeholder),
            protected,
            flags=re.IGNORECASE,
        )

    parts = re.split(r"(?<=[.!?])\s+", protected)
    sentences = []
    for part in parts:
        sentence = _normalize_spaces(part.replace(placeholder, "."))
        if sentence:
            sentences.append(sentence)
    return sentences


def _ensure_sentence(text: str, fallback: str) -> str:
    sentence = _normalize_spaces(text) or fallback
    if sentence and sentence[-1] not in ".!?":
        sentence += "."
    return sentence


def _find_metric_sentence(sentences: Sequence[str]) -> str:
    for sentence in sentences:
        lowered = sentence.lower()
        if re.search(r"\d", sentence):
            return sentence
        if any(hint in lowered for hint in _METRIC_HINTS):
            return sentence
    return ""


def _find_impact_sentence(sentences: Sequence[str]) -> str:
    for sentence in sentences:
        lowered = sentence.lower()
        if any(hint in lowered for hint in _IMPACT_HINTS):
            return sentence
    return ""


def _compose_narrative_summary(what: str, key: str, why: str) -> str:
    primary = [_ensure_sentence(what, "No source summary was generated.")]

    if key and key != _FALLBACK_KEY_DETAIL:
        key_sentence = _ensure_sentence(key, _FALLBACK_KEY_DETAIL + ".")
        if key_sentence != primary[0]:
            primary.append(key_sentence)

    why_sentence = _sentence_from_clause(why)
    if why_sentence and why_sentence not in primary:
        primary.append(why_sentence)

    return " ".join(primary)


def standardize_story_summary(
    summary: str,
    story_bucket: str = STORY_BUCKET_OTHER,
    story_title: str = "",
) -> str:
    text = _normalize_spaces(summary)
    if not text:
        return _compose_narrative_summary(
            "No source summary was generated.",
            _FALLBACK_KEY_DETAIL,
            "",
        )
    if _is_failure_summary(text):
        return text

    existing_what = _extract_labeled(text, _WHAT_LABEL, (_KEY_LABEL, _WHY_LABEL))
    existing_key = _extract_labeled(text, _KEY_LABEL, (_WHY_LABEL,))
    existing_why = _extract_labeled(text, _WHY_LABEL, ())

    normalized = text
    for token in (_WHAT_LABEL, _KEY_LABEL, _WHY_LABEL, "Finding:", "Evidence:", "Why it matters:"):
        normalized = re.sub(re.escape(token), "", normalized, flags=re.IGNORECASE)

    sentences = _split_sentences(normalized)

    what = existing_what or (sentences[0] if sentences else "")

    key_candidates = [sentence for sentence in sentences if sentence != what]
    key = existing_key or _find_metric_sentence(key_candidates)
    if not key and key_candidates:
        key = key_candidates[0]
    if not key:
        key = _FALLBACK_KEY_DETAIL

    why_candidates = [sentence for sentence in sentences if sentence not in (what, key)]
    why = existing_why or _find_impact_sentence(why_candidates)
    if not why and why_candidates:
        why = why_candidates[0]

    return _compose_narrative_summary(what, key, why)


def classify_story_bucket(
    title: str = "",
    summary: str = "",
    url: str = "",
    tag: str = "",
    is_paper: bool = False,
) -> str:
    if is_paper:
        return STORY_BUCKET_RESEARCH

    tag_text = (tag or "").lower()
    if _count_matches(tag_text, _RESEARCH_HINTS):
        return STORY_BUCKET_RESEARCH
    if _count_matches(tag_text, _INDUSTRY_HINTS):
        return STORY_BUCKET_INDUSTRY_INVESTMENT
    if _count_matches(tag_text, _POLICY_SECURITY_HINTS):
        return STORY_BUCKET_POLICY_SECURITY
    if _count_matches(tag_text, _INFRASTRUCTURE_ECOSYSTEM_HINTS):
        return STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM

    combined_text = " ".join(part for part in (title, summary, url) if part).lower()
    parsed_url = urlparse(url or "")
    domain = parsed_url.netloc.lower()
    path = parsed_url.path.lower()

    scores = {
        STORY_BUCKET_RESEARCH: _count_matches(combined_text, _RESEARCH_HINTS),
        STORY_BUCKET_INDUSTRY_INVESTMENT: _count_matches(combined_text, _INDUSTRY_HINTS),
        STORY_BUCKET_POLICY_SECURITY: _count_matches(combined_text, _POLICY_SECURITY_HINTS),
        STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM: _count_matches(combined_text, _INFRASTRUCTURE_ECOSYSTEM_HINTS),
        STORY_BUCKET_OTHER: 0,
    }

    if _has_academic_domain(domain):
        scores[STORY_BUCKET_RESEARCH] += 2
    if _has_government_domain(domain):
        scores[STORY_BUCKET_POLICY_SECURITY] += 2
    if any(hint in path for hint in _RESEARCH_URL_HINTS):
        scores[STORY_BUCKET_RESEARCH] += 1

    if max(scores.values()) <= 0:
        return STORY_BUCKET_OTHER

    return sorted(scores.items(), key=lambda item: (-item[1], _BUCKET_PRIORITY[item[0]]))[0][0]


def enrich_story(result: Dict[str, object]) -> Dict[str, object]:
    enriched = result.copy()
    story_bucket = classify_story_bucket(
        title=str(result.get("title", "") or ""),
        summary=str(result.get("summary", "") or ""),
        url=str(result.get("url", "") or ""),
        tag=str(result.get("tag", "") or ""),
        is_paper=bool(result.get("is_paper")),
    )
    enriched["story_bucket"] = story_bucket
    enriched["summary"] = standardize_story_summary(
        str(result.get("summary", "") or ""),
        story_bucket=story_bucket,
        story_title=str(result.get("title", "") or ""),
    )
    return enriched


def _topic_signature(title: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
    filtered = [token for token in tokens if token not in _STOPWORDS]
    if not filtered:
        return ""
    return " ".join(filtered[:6])


def _canonical_story_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.netloc:
        return ""
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+$", "", parsed.path or "") or "/"
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, value))
    return urlunparse(
        (
            (parsed.scheme or "https").lower(),
            netloc,
            path,
            "",
            urlencode(sorted(query_items)),
            "",
        )
    )


def _title_tokens(title: str) -> List[str]:
    tokens = []
    for token in re.findall(r"[a-z0-9]+", (title or "").lower()):
        if len(token) <= 1 or token in _STOPWORDS:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


def _titles_represent_same_story(left: str, right: str) -> bool:
    left_tokens = _title_tokens(left)
    right_tokens = _title_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    shared = left_set & right_set
    if len(shared) < 3:
        return False
    union = left_set | right_set
    jaccard = len(shared) / max(1, len(union))
    containment = len(shared) / max(1, min(len(left_set), len(right_set)))
    normalized_left = " ".join(left_tokens)
    normalized_right = " ".join(right_tokens)
    sequence_ratio = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    return jaccard >= 0.62 or containment >= 0.8 or sequence_ratio >= 0.78


_GENERIC_TOPIC_TOKENS = {
    "advance",
    "announc",
    "company",
    "computer",
    "computing",
    "development",
    "industry",
    "innovation",
    "news",
    "processor",
    "quantum",
    "research",
    "researcher",
    "result",
    "story",
    "system",
    "technology",
    "update",
}

_TOPIC_ACTION_GROUPS = {
    "funding": {
        "back",
        "financ",
        "fund",
        "funding",
        "invest",
        "investment",
        "raise",
        "raised",
        "round",
        "secure",
        "secured",
    },
    "launch": {"debut", "introduc", "launch", "release", "unveil"},
    "partnership": {"collaborat", "partner", "partnership", "team"},
    "facility": {"campus", "center", "centre", "facility", "factory", "open", "site"},
    "contract": {"award", "contract", "procurement", "select", "selected"},
    "acquisition": {"acquire", "acquisition", "buy", "merger"},
    "research_result": {
        "achiev",
        "benchmark",
        "demonstrat",
        "experiment",
        "publish",
        "report",
        "study",
    },
}


def _topic_tokens(text: str) -> set:
    return {
        token
        for token in _title_tokens(text)
        if token not in _GENERIC_TOPIC_TOKENS
    }


def _topic_action_groups(story: Dict[str, object]) -> set:
    tokens = set(
        _title_tokens(
            " ".join(
                str(story.get(key, "") or "")
                for key in ("title", "title_seed", "summary")
            )
        )
    )
    groups = set()
    for group, hints in _TOPIC_ACTION_GROUPS.items():
        if any(any(token.startswith(hint) for hint in hints) for token in tokens):
            groups.add(group)
    return groups


def _stories_represent_same_topic(
    left: Dict[str, object],
    right: Dict[str, object],
) -> bool:
    """Detect one news event covered with materially different headlines."""
    left_title = str(left.get("title", "") or left.get("title_seed", "") or "")
    right_title = str(right.get("title", "") or right.get("title_seed", "") or "")
    if _titles_represent_same_story(left_title, right_title):
        return True
    left_title_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", left_title))
    right_title_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", right_title))
    if left_title_numbers and right_title_numbers and not (left_title_numbers & right_title_numbers):
        return False

    left_title_tokens = _topic_tokens(left_title)
    right_title_tokens = _topic_tokens(right_title)
    shared_title = left_title_tokens & right_title_tokens
    if not shared_title:
        return False

    left_text = " ".join(
        str(left.get(key, "") or "") for key in ("title", "title_seed", "summary")
    )
    right_text = " ".join(
        str(right.get(key, "") or "") for key in ("title", "title_seed", "summary")
    )
    left_tokens = _topic_tokens(left_text)
    right_tokens = _topic_tokens(right_text)
    shared = left_tokens & right_tokens
    if len(shared) < 3:
        return False

    combined_containment = len(shared) / max(1, min(len(left_tokens), len(right_tokens)))
    shared_actions = _topic_action_groups(left) & _topic_action_groups(right)
    left_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", left_text))
    right_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", right_text))
    shared_numbers = left_numbers & right_numbers

    if len(shared_title) >= 2 and (shared_actions or combined_containment >= 0.42):
        return True
    if shared_actions and combined_containment >= 0.34:
        return True
    if shared_numbers and combined_containment >= 0.3:
        return True
    return False


def _story_passes_checks(story: Dict[str, object]) -> bool:
    if story.get("qa_flags"):
        return False
    grounding = story.get("grounding")
    return not isinstance(grounding, dict) or grounding.get("passed") is not False


def _story_preference(story: Dict[str, object]) -> Tuple[int, int, int]:
    source = str(story.get("source", "") or "").lower()
    summary_words = len(str(story.get("summary", "") or "").split())
    return (
        1 if _story_passes_checks(story) else 0,
        1 if source in {"spreadsheet", "sheet"} else 0,
        min(summary_words, 140),
    )


def _source_reference(story: Dict[str, object]) -> Dict[str, str]:
    return {
        "url": str(story.get("url", "") or ""),
        "title": str(story.get("title", "") or story.get("title_seed", "") or ""),
        "source": str(story.get("source", "") or ""),
    }


def _source_references(story: Dict[str, object]) -> List[Dict[str, str]]:
    references = []
    candidates = [_source_reference(story)]
    candidates.extend(
        item
        for item in (story.get("related_sources", []) or [])
        if isinstance(item, dict)
    )
    seen = set()
    for candidate in candidates:
        url = str(candidate.get("url", "") or "")
        if not url or url in seen:
            continue
        references.append(
            {
                "url": url,
                "title": str(candidate.get("title", "") or ""),
                "source": str(candidate.get("source", "") or ""),
            }
        )
        seen.add(url)
    return references


def _summary_sentences(summary: str) -> List[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", summary or "")
        if sentence.strip()
    ]


def _sentence_is_redundant(sentence: str, existing_sentences: Sequence[str]) -> bool:
    candidate_tokens = set(_title_tokens(sentence))
    if not candidate_tokens:
        return True
    normalized_candidate = " ".join(_title_tokens(sentence))
    for existing in existing_sentences:
        existing_tokens = set(_title_tokens(existing))
        if not existing_tokens:
            continue
        shared = candidate_tokens & existing_tokens
        containment = len(shared) / max(1, min(len(candidate_tokens), len(existing_tokens)))
        sequence_ratio = SequenceMatcher(
            None,
            normalized_candidate,
            " ".join(_title_tokens(existing)),
        ).ratio()
        if containment >= 0.72 or sequence_ratio >= 0.74:
            return True
    return False


def _consolidate_story_summaries(
    primary: Dict[str, object],
    grouped_stories: Sequence[Dict[str, object]],
    max_words: int = 145,
) -> str:
    """Add distinct, grounded facts from duplicate coverage to one summary."""
    primary_summary = str(primary.get("summary", "") or "").strip()
    if not primary_summary:
        return primary_summary

    sentences = _summary_sentences(primary_summary)
    combined_tokens = set(_title_tokens(primary_summary))
    combined_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", primary_summary))

    for story in grouped_stories:
        if story is primary or not _story_passes_checks(story):
            continue
        summary = str(story.get("summary", "") or "").strip()
        if not summary or summary.lower().startswith("unable to generate"):
            continue
        for sentence in _summary_sentences(summary):
            sentence_tokens = set(_title_tokens(sentence))
            if len(sentence_tokens) < 6 or _sentence_is_redundant(sentence, sentences):
                continue
            sentence_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", sentence))
            novel_tokens = sentence_tokens - combined_tokens
            adds_number = bool(sentence_numbers - combined_numbers)
            if not adds_number and len(novel_tokens) < 3:
                continue
            if len(" ".join(sentences + [sentence]).split()) > max_words:
                continue
            sentences.append(sentence)
            combined_tokens.update(sentence_tokens)
            combined_numbers.update(sentence_numbers)

    return " ".join(sentences)


def deduplicate_stories(results: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    """Merge same-topic coverage into one consolidated newsletter story.

    Exact URLs, strong headline matches, and same-event topic matches are
    grouped across the complete issue. The best grounded version becomes the
    visible story; complementary facts and every source URL are retained.
    """
    groups: List[Dict[str, object]] = []

    for index, raw_story in enumerate(results or []):
        story = raw_story.copy()
        story.setdefault("_index", index)
        story_url = _canonical_story_url(str(story.get("url", "") or ""))
        story_title = str(story.get("title", "") or story.get("title_seed", "") or "")
        match = None
        for group in groups:
            if story_url and story_url in group["urls"]:
                match = group
                break
            if any(
                _stories_represent_same_topic(story, grouped_story)
                for grouped_story in group["stories"]
            ):
                match = group
                break

        if match is None:
            references = _source_references(story)
            groups.append(
                {
                    "stories": [story],
                    "urls": {story_url} if story_url else set(),
                    "sources": references,
                    "story_ids": [str(story.get("story_id", "") or "")],
                }
            )
            continue

        if story_url:
            match["urls"].add(story_url)
        existing_source_urls = {item["url"] for item in match["sources"]}
        for reference in _source_references(story):
            if reference["url"] not in existing_source_urls:
                match["sources"].append(reference)
                existing_source_urls.add(reference["url"])
        match["story_ids"].append(str(story.get("story_id", "") or ""))
        match["stories"].append(story)

    deduplicated = []
    for group in groups:
        grouped_stories = group["stories"]
        primary = max(grouped_stories, key=_story_preference)
        story = primary.copy()
        story["_index"] = min(int(item.get("_index", 0)) for item in grouped_stories)
        story["summary"] = _consolidate_story_summaries(primary, grouped_stories)
        story["related_sources"] = group["sources"]
        duplicate_ids = [story_id for story_id in group["story_ids"] if story_id and story_id != str(story.get("story_id", ""))]
        if duplicate_ids:
            story["duplicate_story_ids"] = duplicate_ids
            story["consolidated_story_ids"] = [
                story_id for story_id in group["story_ids"] if story_id
            ]
            story["consolidated_source_count"] = len(group["sources"])
        deduplicated.append(story)
    return sorted(deduplicated, key=lambda item: int(item.get("_index", 0)))


def _extract_url_date(url: str):
    if not url:
        return None

    for pattern, fmt in (
        (r"/(20\d{2})/(\d{1,2})/(\d{1,2})/", "%Y-%m-%d"),
        (r"(20\d{2})(\d{2})(\d{2})", "%Y-%m-%d"),
        (r"(20\d{2})-(\d{2})-(\d{2})", "%Y-%m-%d"),
    ):
        match = re.search(pattern, url)
        if not match:
            continue
        year, month, day = match.groups()
        try:
            return datetime.strptime(f"{int(year):04d}-{int(month):02d}-{int(day):02d}", fmt)
        except ValueError:
            continue
    return None


def _score_source_quality(url: str) -> int:
    lowered = (url or "").lower()
    parsed = urlparse(lowered)
    domain = parsed.netloc

    if any(hint in lowered for hint in _LOW_TRUST_DOMAINS):
        return -2
    if any(hint in domain for hint in _PRESS_RELEASE_HINTS):
        return -1
    if any(hint in domain for hint in _HIGH_TRUST_DOMAINS):
        return 2
    return 0


def _score_impact(title: str, summary: str) -> int:
    text = f"{title} {summary}".lower()
    score = 0
    if re.search(r"\d", text):
        score += 1
    score += min(2, _count_matches(text, _METRIC_HINTS))
    score += min(2, _count_matches(text, _IMPACT_HINTS))
    return score


def _score_recency(url: str) -> int:
    extracted = _extract_url_date(url)
    if not extracted:
        return 0
    delta_days = max(0, (datetime.utcnow() - extracted).days)
    if delta_days <= 7:
        return 3
    if delta_days <= 30:
        return 2
    if delta_days <= 90:
        return 1
    return 0


def _score_story(story: Dict[str, object], index: int) -> int:
    score = 0
    score += _score_source_quality(str(story.get("url", "") or ""))
    score += _score_impact(str(story.get("title", "") or ""), str(story.get("summary", "") or ""))
    score += _score_recency(str(story.get("url", "") or ""))

    if story.get("is_paper"):
        score += 2
    if story.get("image_url"):
        score += 1

    bucket = story.get("story_bucket")
    if bucket in (STORY_BUCKET_RESEARCH, STORY_BUCKET_INDUSTRY_INVESTMENT, STORY_BUCKET_POLICY_SECURITY):
        score += 2
    elif bucket == STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM:
        score += 1

    score += max(0, 5 - min(index, 5))
    return score


def order_stories(results: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    annotated: List[Tuple[int, Dict[str, object]]] = []
    for index, result in enumerate(results):
        enriched = enrich_story(result)
        enriched["_index"] = index
        annotated.append((index, enriched))
    ordered = sorted(
        annotated,
        key=lambda item: (_BUCKET_PRIORITY[item[1]["story_bucket"]], item[0]),
    )
    return [story for _, story in ordered]


def curate_stories(
    results: Iterable[Dict[str, object]],
    primary_limit: int = _DEFAULT_PRIMARY_LIMIT,
    overflow_limit: int = _DEFAULT_OVERFLOW_LIMIT,
) -> Dict[str, object]:
    prepared: List[Dict[str, object]] = []
    source_results = list(results or [])
    deduplicated_results = deduplicate_stories(source_results)
    for index, result in enumerate(deduplicated_results):
        enriched = enrich_story(result)
        enriched["_index"] = index
        enriched["_score"] = _score_story(enriched, index)
        prepared.append(enriched)

    primary = sorted(
        prepared,
        key=lambda item: (_BUCKET_PRIORITY[item["story_bucket"]], int(item.get("_index", 0))),
    )

    channel_counts = {bucket: 0 for bucket in STORY_BUCKET_SEQUENCE}
    for story in primary:
        channel_counts[story["story_bucket"]] += 1

    return {
        "primary": primary,
        "overflow": [],
        "channel_counts": channel_counts,
        "input_count": len(source_results),
        "deduplicated_count": len(source_results) - len(deduplicated_results),
    }


def group_stories(results: Iterable[Dict[str, object]]) -> List[Tuple[str, List[Dict[str, object]]]]:
    ordered = order_stories(results)
    groups: List[Tuple[str, List[Dict[str, object]]]] = []
    for bucket in STORY_BUCKET_SEQUENCE:
        stories = [story for story in ordered if story.get("story_bucket") == bucket]
        if stories:
            groups.append((bucket, stories))
    return groups


def build_story_digest(results: Iterable[Dict[str, object]]) -> str:
    ordered = order_stories(results)
    blocks = []
    for story in ordered:
        label = STORY_BUCKET_LABELS.get(story.get("story_bucket"), STORY_BUCKET_LABELS[STORY_BUCKET_OTHER])
        title = str(story.get("title", "") or "").strip()
        summary = str(story.get("summary", "") or "").strip()
        url = str(story.get("url", "") or "").strip()
        blocks.append(f"[{label}]\nTitle: {title}\nSummary: {summary}\nURL: {url}")
    return "\n\n".join(blocks)
