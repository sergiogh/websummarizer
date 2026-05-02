import re

from summary_generator import normalize_text


def format_paper_title(title):
    """Format a paper title by adding [PAPER] prefix and removing publisher names."""
    if not title:
        return title

    publisher_patterns = [
        r'\b(arXiv|arxiv)\b',
        r'\b(Nature|nature)\b',
        r'\b(Science|science)\b',
        r'\b(bioRxiv|biorxiv|medRxiv|medrxiv)\b',
        r'\b(IEEE|ACM|Springer|Elsevier|PNAS|Cell|Lancet)\b',
        r'\b(Published in|from|via|on)\s+(arXiv|Nature|Science|bioRxiv|medRxiv|IEEE|ACM)\b',
    ]

    cleaned_title = title
    for pattern in publisher_patterns:
        cleaned_title = re.sub(pattern, '', cleaned_title, flags=re.IGNORECASE)

    cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()

    if not cleaned_title.startswith('[PAPER]'):
        cleaned_title = f"[PAPER] {cleaned_title}"

    return cleaned_title


def strip_source_from_title(title):
    if not title:
        return title

    source_terms = [
        "Newswire", "PR Newswire", "Business Wire", "GlobeNewswire", "EIN Presswire",
        "PRWeb", "Accesswire", "Business Insider", "MarketWatch", "Morningstar",
        "Seeking Alpha", "The Quantum Insider", "Quantum Insider", "TechCrunch",
        "Bloomberg", "Reuters", "Associated Press", "AP News", "The Guardian",
        "Financial Times", "Wall Street Journal", "WSJ", "New York Times", "The Times",
        "BBC", "CNN", "CNBC", "Forbes", "VentureBeat", "The Verge", "Ars Technica",
        "MIT Technology Review", "MIT Tech Review", "Phys.org", "ScienceDaily",
        "SciTechDaily", "TechXplore", "EurekAlert", "Press Release",
        "arXiv", "bioRxiv", "medRxiv", "Nature", "Science", "IEEE", "ACM",
        "Springer", "Elsevier", "PNAS", "Cell", "Lancet"
    ]
    source_union = "|".join(re.escape(term) for term in source_terms)

    cleaned = re.sub(r'^\s*Title:\s*', '', title, flags=re.IGNORECASE)
    cleaned = re.sub(rf'\s*[-–—|:]\s*(?:{source_union})\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf'\s*\(\s*(?:{source_union})\s*\)\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf'\s*\[\s*(?:{source_union})\s*\]\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf'\s+(?:via|from|on)\s+(?:{source_union})\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf'^(?:from|via)\s+(?:{source_union})\s*[:-]\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*[-–—|:]\s*(?:https?://)?(?:www\.)?\S+\.\w{2,}.*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned


def sanitize_story_title(title, is_paper=False):
    cleaned = normalize_text(title)
    cleaned = strip_source_from_title(cleaned)
    if is_paper:
        cleaned = format_paper_title(cleaned)
    return cleaned
