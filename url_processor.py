import re
import requests
from bs4 import BeautifulSoup
from typing import Dict, Optional
import time
import random
from urllib.parse import urljoin, urlparse
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from alternative_fetcher import AlternativeFetcher
from download_config import TIMEOUTS, RETRY_CONFIG, USER_AGENTS, PROBLEMATIC_DOMAINS, SHORT_URL_PATTERNS, HEADER_STRATEGIES, DELAY_RANGES

try:
    import trafilatura
except Exception:
    trafilatura = None

try:
    from readability import Document
except Exception:
    Document = None

# Disable SSL warnings for problematic sites
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class UrlProcessor:
    def __init__(self, url: str):
        self.url: str = url
        self.content: Optional[str] = None
        self.metadata: Dict[str, object] = {}
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a session with retry strategy and proper configuration."""
        session = requests.Session()
        
        # Configure retry strategy from config
        retry_strategy = Retry(
            total=RETRY_CONFIG['max_retries'],
            backoff_factor=RETRY_CONFIG['backoff_factor'],
            status_forcelist=RETRY_CONFIG['status_forcelist'],
            allowed_methods=RETRY_CONFIG['allowed_methods']
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session

    def _get_headers(self, strategy: str = 'standard') -> dict:
        """Get headers based on strategy to avoid detection."""
        headers = HEADER_STRATEGIES.get(strategy, HEADER_STRATEGIES['standard']).copy()
        headers['User-Agent'] = random.choice(USER_AGENTS)
        return headers

    def _is_short_url(self, url: str) -> bool:
        """Check if URL is a short URL that needs special handling."""
        return any(pattern in url for pattern in SHORT_URL_PATTERNS)
    
    def _is_problematic_domain(self, url: str) -> bool:
        """Check if URL is from a problematic domain."""
        parsed_url = urlparse(url)
        return any(domain in parsed_url.netloc for domain in PROBLEMATIC_DOMAINS)

    def _expand_short_url(self, url: str) -> str:
        """Expand short URLs to get the actual destination."""
        try:
            response = self.session.head(url, allow_redirects=True, timeout=10)
            return response.url
        except:
            return url

    def download_content(self) -> None:
        """Download the content of the URL with multiple fallback strategies."""
        original_url = self.url
        # First, try to expand short URLs
        if self._is_short_url(self.url):
            print(f"Detected short URL, attempting to expand: {self.url}")
            expanded_url = self._expand_short_url(self.url)
            if expanded_url != self.url:
                print(f"Expanded URL: {expanded_url}")
                self.url = expanded_url
        self.metadata.update(
            {
                "submitted_url": original_url,
                "final_url": self.url,
                "redirected": original_url != self.url,
                "short_url": self._is_short_url(original_url),
            }
        )
        
        # Check if this is a problematic domain and adjust strategy
        is_problematic = self._is_problematic_domain(self.url)
        if is_problematic:
            print(f"Detected problematic domain: {self.url}")
        
        strategies = [
            self._strategy_standard,
            self._strategy_with_delay,
            self._strategy_short_timeout,
            self._strategy_no_ssl_verify,
            self._strategy_different_headers,
            self._strategy_problematic_domain if is_problematic else None
        ]
        
        # Remove None strategies
        strategies = [s for s in strategies if s is not None]
        
        for i, strategy in enumerate(strategies):
            try:
                print(f"Trying strategy {i+1} for {self.url}")
                if strategy():
                    print(f"Successfully downloaded content using strategy {i+1}")
                    return
            except Exception as e:
                print(f"Strategy {i+1} failed: {e}")
                continue
        
        # If all strategies fail, try alternative fetcher
        print(f"All primary strategies failed for {self.url}, trying alternative fetcher...")
        try:
            alt_fetcher = AlternativeFetcher(self.url)
            if alt_fetcher.fetch_with_selenium_fallback():
                self.content = alt_fetcher.get_content()
                if self.content:
                    print(f"Successfully downloaded content using alternative fetcher")
                    return
        except Exception as e:
            print(f"Alternative fetcher also failed: {e}")
        
        print(f"All strategies failed for {self.url}")
        self.content = None

    def _strategy_standard(self) -> bool:
        """Standard download strategy."""
        headers = self._get_headers('standard')
        response = self.session.get(
            self.url, 
            headers=headers, 
            timeout=TIMEOUTS['standard'],
            verify=True
        )
        response.raise_for_status()
        self.content = response.text
        self.url = response.url or self.url
        self.metadata.update({"final_url": self.url, "redirected": self.metadata.get("submitted_url", self.url) != self.url})
        return True

    def _strategy_with_delay(self) -> bool:
        """Strategy with random delay to avoid rate limiting."""
        delay_range = DELAY_RANGES['medium']
        time.sleep(random.uniform(delay_range[0], delay_range[1]))
        headers = self._get_headers('standard')
        response = self.session.get(
            self.url, 
            headers=headers, 
            timeout=TIMEOUTS['long'],
            verify=True
        )
        response.raise_for_status()
        self.content = response.text
        self.url = response.url or self.url
        self.metadata.update({"final_url": self.url, "redirected": self.metadata.get("submitted_url", self.url) != self.url})
        return True

    def _strategy_short_timeout(self) -> bool:
        """Strategy with shorter timeout for quick failures."""
        headers = self._get_headers('standard')
        response = self.session.get(
            self.url, 
            headers=headers, 
            timeout=TIMEOUTS['short'],
            verify=True
        )
        response.raise_for_status()
        self.content = response.text
        self.url = response.url or self.url
        self.metadata.update({"final_url": self.url, "redirected": self.metadata.get("submitted_url", self.url) != self.url})
        return True

    def _strategy_no_ssl_verify(self) -> bool:
        """Strategy without SSL verification for problematic sites."""
        headers = self._get_headers('standard')
        response = self.session.get(
            self.url, 
            headers=headers, 
            timeout=TIMEOUTS['standard'],
            verify=False
        )
        response.raise_for_status()
        self.content = response.text
        self.url = response.url or self.url
        self.metadata.update({"final_url": self.url, "redirected": self.metadata.get("submitted_url", self.url) != self.url})
        return True

    def _strategy_different_headers(self) -> bool:
        """Strategy with different headers to avoid detection."""
        headers = self._get_headers('api')
        response = self.session.get(
            self.url, 
            headers=headers, 
            timeout=TIMEOUTS['standard'],
            verify=True
        )
        response.raise_for_status()
        self.content = response.text
        self.url = response.url or self.url
        self.metadata.update({"final_url": self.url, "redirected": self.metadata.get("submitted_url", self.url) != self.url})
        return True
    
    def _strategy_problematic_domain(self) -> bool:
        """Special strategy for problematic domains like Chinese news sites."""
        headers = self._get_headers('mobile')
        # Add longer delay for problematic domains
        time.sleep(random.uniform(2, 4))
        response = self.session.get(
            self.url, 
            headers=headers, 
            timeout=TIMEOUTS['very_long'],
            verify=False,
            allow_redirects=True
        )
        response.raise_for_status()
        self.content = response.text
        self.url = response.url or self.url
        self.metadata.update({"final_url": self.url, "redirected": self.metadata.get("submitted_url", self.url) != self.url})
        return True

    def strip_html(self) -> None:
        """Strip the HTML tags from the content."""
        if self.content is not None:
            extraction = extract_article_payload(self.content, self.url)
            inherited = self.metadata.copy()
            inherited.update(extraction["metadata"])
            self.metadata = inherited
            self.content = extraction["text"]
        else:
            print(f"No content to strip HTML from for {self.url}")


def _clean_text(text: str) -> str:
    lines = (line.strip() for line in (text or "").splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    return re.sub(r"\s+", " ", " ".join(chunk for chunk in chunks if chunk)).strip()


def _meta_content(soup: BeautifulSoup, *selectors: str) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        content = node.get("content") or node.get_text(" ", strip=True)
        if content:
            return _clean_text(content)
    return ""


def _remove_boilerplate(soup: BeautifulSoup) -> None:
    for node in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "svg",
            "canvas",
            "form",
            "nav",
            "footer",
            "aside",
            "header",
        ]
    ):
        node.decompose()

    noisy_selectors = [
        "[class*=cookie]",
        "[id*=cookie]",
        "[class*=consent]",
        "[id*=consent]",
        "[class*=advert]",
        "[id*=advert]",
        "[class*=subscribe]",
        "[id*=subscribe]",
        "[class*=newsletter]",
        "[class*=related]",
        "[id*=related]",
        "[class*=recommend]",
        "[class*=social]",
        "[class*=share]",
        "[aria-label*='share']",
    ]
    for selector in noisy_selectors:
        for node in soup.select(selector):
            node.decompose()


def _score_candidate_text(text: str) -> int:
    cleaned = _clean_text(text)
    if not cleaned:
        return 0
    sentence_count = len(re.findall(r"[.!?](?:\s|$)", cleaned))
    paragraphish_count = len(re.findall(r"\s{2,}", text or ""))
    return len(cleaned) + sentence_count * 120 + paragraphish_count * 80


def _select_article_node(soup: BeautifulSoup):
    selectors = [
        "article",
        "[itemtype*='schema.org/Article']",
        "[itemtype*='schema.org/NewsArticle']",
        "[role='main'] article",
        "main article",
        "main",
        ".article-body",
        ".article__body",
        ".entry-content",
        ".post-content",
        ".story-body",
        "[data-testid='article-body']",
        "[data-test='article-body']",
        "[data-article-body]",
    ]

    candidates = []
    for selector in selectors:
        candidates.extend(soup.select(selector))

    if not candidates and soup.body:
        candidates = [soup.body]

    if not candidates:
        return None, "none"

    best = max(candidates, key=lambda node: _score_candidate_text(node.get_text("\n", strip=True)))
    selector_name = getattr(best, "name", "unknown")
    return best, selector_name


def _extract_with_trafilatura(html_content: str, url: str = "") -> str:
    if not trafilatura:
        return ""
    try:
        extracted = trafilatura.extract(
            html_content,
            url=url or None,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        return _clean_text(extracted or "")
    except Exception:
        return ""


def _extract_with_readability(html_content: str) -> str:
    if not Document:
        return ""
    try:
        doc = Document(html_content)
        readable_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(readable_html or "", "html.parser")
        _remove_boilerplate(soup)
        return _clean_text(soup.get_text("\n", strip=True))
    except Exception:
        return ""


def extract_article_payload(html_content: str, url: str = "") -> Dict[str, object]:
    """Extract article-focused text and metadata from downloaded HTML.

    The goal is to summarize the selected story, not every word that happens to
    appear on the page. This keeps related links and site chrome out of the model
    context whenever the page exposes a usable article/main body.
    """
    soup = BeautifulSoup(html_content or "", "html.parser")

    canonical = _meta_content(soup, "link[rel='canonical']")
    if not canonical:
        canonical_node = soup.select_one("link[rel='canonical']")
        if canonical_node and canonical_node.get("href"):
            canonical = urljoin(url, canonical_node.get("href"))

    metadata = {
        "source_url": url,
        "canonical_url": canonical,
        "html_title": _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else "",
        "og_title": _meta_content(soup, "meta[property='og:title']"),
        "twitter_title": _meta_content(soup, "meta[name='twitter:title']"),
        "description": _meta_content(
            soup,
            "meta[name='description']",
            "meta[property='og:description']",
            "meta[name='twitter:description']",
        ),
        "h1": _clean_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else "",
    }

    trafilatura_text = _extract_with_trafilatura(html_content, url)
    readability_text = _extract_with_readability(html_content)

    _remove_boilerplate(soup)
    article_node, selector = _select_article_node(soup)
    article_text = _clean_text(article_node.get_text("\n", strip=True)) if article_node else ""
    full_text = _clean_text(soup.get_text("\n", strip=True))

    extraction_status = "article"
    selected_text = article_text
    extraction_method = "beautifulsoup_article"
    candidates = [
        ("trafilatura", trafilatura_text),
        ("readability", readability_text),
        ("beautifulsoup_article", article_text),
    ]
    best_method, best_text = max(candidates, key=lambda item: _score_candidate_text(item[1]))
    if best_text and len(best_text) >= 500:
        selected_text = best_text
        extraction_method = best_method
        extraction_status = "article"
    if len(article_text) < 700 and len(full_text) > len(article_text):
        if _score_candidate_text(full_text) > _score_candidate_text(selected_text) * 1.25:
            selected_text = full_text
            extraction_method = "beautifulsoup_full_page"
        extraction_status = "fallback_full_page"
    if len(selected_text) < 300:
        extraction_status = "insufficient_content"

    metadata.update(
        {
            "extraction_status": extraction_status,
            "article_selector": selector,
            "extraction_method": extraction_method,
            "article_text_length": len(article_text),
            "trafilatura_text_length": len(trafilatura_text),
            "readability_text_length": len(readability_text),
            "clean_text_length": len(selected_text),
        }
    )

    return {"text": selected_text, "metadata": metadata}
