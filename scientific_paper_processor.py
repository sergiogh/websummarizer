import requests
from urllib.parse import urlparse, urljoin
from typing import Optional, Tuple
import re
from bs4 import BeautifulSoup
import PyPDF2
import io
from openai import OpenAI
import os
from prompt_loader import get_prompt


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

class ScientificPaperProcessor:
    """Process scientific papers from various sources like arXiv, Nature, Science, etc."""
    
    # Patterns to detect scientific paper URLs
    SCIENTIFIC_DOMAINS = {
        'arxiv.org': 'arxiv',
        'arxiv.org/abs': 'arxiv',
        'arxiv.org/pdf': 'arxiv',
        'nature.com': 'nature',
        'www.nature.com': 'nature',
        'science.org': 'science',
        'www.science.org': 'science',
        'sciencemag.org': 'science',
        'ieee.org': 'ieee',
        'acm.org': 'acm',
        'springer.com': 'springer',
        'link.springer.com': 'springer',
        'sciencedirect.com': 'sciencedirect',
        'elsevier.com': 'elsevier',
        'biorxiv.org': 'biorxiv',
        'medrxiv.org': 'medrxiv',
        'pnas.org': 'pnas',
        'cell.com': 'cell',
        'thelancet.com': 'lancet',
    }
    
    def __init__(self, url: str):
        self.url = url
        self.paper_type: Optional[str] = None
        self.full_text: Optional[str] = None
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4.1')
    
    def is_scientific_paper(self) -> bool:
        """Check if the URL points to a scientific paper."""
        parsed_url = urlparse(self.url)
        domain = parsed_url.netloc.lower()
        path = parsed_url.path.lower()
        full_url = self.url.lower()
        
        # Check for arXiv patterns (can be in domain or path)
        if 'arxiv' in domain:
            if '/abs/' in path or '/pdf/' in path or '.pdf' in path:
                self.paper_type = 'arxiv'
                return True
        
        # Check for bioRxiv/medRxiv patterns
        if 'biorxiv' in domain or 'medrxiv' in domain:
            if '/content/' in path:
                self.paper_type = 'biorxiv' if 'biorxiv' in domain else 'medrxiv'
                return True
        
        # Check for exact domain matches (without path components)
        domain_only_patterns = {
            'nature.com': 'nature',
            'www.nature.com': 'nature',
            'science.org': 'science',
            'www.science.org': 'science',
            'sciencemag.org': 'science',
            'ieee.org': 'ieee',
            'acm.org': 'acm',
            'springer.com': 'springer',
            'link.springer.com': 'springer',
            'sciencedirect.com': 'sciencedirect',
            'elsevier.com': 'elsevier',
            'pnas.org': 'pnas',
            'cell.com': 'cell',
            'thelancet.com': 'lancet',
        }
        
        for domain_pattern, paper_type in domain_only_patterns.items():
            if domain_pattern in domain:
                # Additional check: make sure it's likely a paper URL
                # Nature/Science papers often have article IDs in the path
                if paper_type in ['nature', 'science']:
                    if '/articles/' in path or '/article/' in path:
                        self.paper_type = paper_type
                        return True
                else:
                    self.paper_type = paper_type
                    return True
        
        return False
    
    def download_arxiv_paper(self) -> Optional[str]:
        """Download and extract text from an arXiv paper."""
        try:
            # Convert abs URL to PDF URL if needed
            pdf_url = self.url
            if '/abs/' in self.url:
                # Extract arXiv ID
                arxiv_id = re.search(r'arxiv\.org/abs/(\d+\.\d+)', self.url)
                if arxiv_id:
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id.group(1)}.pdf"
                else:
                    # Try alternative format
                    arxiv_id = re.search(r'arxiv\.org/abs/([^/]+)', self.url)
                    if arxiv_id:
                        pdf_url = f"https://arxiv.org/pdf/{arxiv_id.group(1)}.pdf"
            
            if not pdf_url.endswith('.pdf'):
                pdf_url = pdf_url + '.pdf'
            
            print(f"Downloading arXiv PDF from: {pdf_url}")
            response = requests.get(pdf_url, timeout=60)
            response.raise_for_status()
            
            # Extract text from PDF
            pdf_file = io.BytesIO(response.content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text_parts = []
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
                except Exception as e:
                    print(f"Error extracting text from page {page_num}: {e}")
                    continue
            
            full_text = '\n\n'.join(text_parts)
            return full_text if full_text.strip() else None
            
        except Exception as e:
            print(f"Error downloading arXiv paper: {e}")
            return None
    
    def download_nature_paper(self) -> Optional[str]:
        """Download and extract text from a Nature paper."""
        try:
            print(f"Downloading Nature paper from: {self.url}")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(self.url, headers=headers, timeout=60)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try to find the main content
            # Nature articles typically have content in specific divs
            content_selectors = [
                'article',
                '[data-test="article-body"]',
                '.article__body',
                '.c-article-body',
                'main article',
            ]
            
            text_parts = []
            for selector in content_selectors:
                elements = soup.select(selector)
                if elements:
                    for elem in elements:
                        # Remove script and style elements
                        for script in elem(["script", "style", "nav", "header", "footer"]):
                            script.decompose()
                        text = elem.get_text(separator='\n', strip=True)
                        if text and len(text) > 500:  # Ensure substantial content
                            text_parts.append(text)
                            break
                    if text_parts:
                        break
            
            # If no specific content found, try to get all text
            if not text_parts:
                # Remove unwanted elements
                for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    script.decompose()
                text = soup.get_text(separator='\n', strip=True)
                if text and len(text) > 500:
                    text_parts.append(text)
            
            full_text = '\n\n'.join(text_parts)
            return full_text if full_text.strip() else None
            
        except Exception as e:
            print(f"Error downloading Nature paper: {e}")
            return None
    
    def download_science_paper(self) -> Optional[str]:
        """Download and extract text from a Science paper."""
        try:
            print(f"Downloading Science paper from: {self.url}")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(self.url, headers=headers, timeout=60)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Science articles typically have content in specific divs
            content_selectors = [
                'article',
                '.article-body',
                '.article__body',
                '[data-article-body]',
                'main article',
            ]
            
            text_parts = []
            for selector in content_selectors:
                elements = soup.select(selector)
                if elements:
                    for elem in elements:
                        # Remove script and style elements
                        for script in elem(["script", "style", "nav", "header", "footer"]):
                            script.decompose()
                        text = elem.get_text(separator='\n', strip=True)
                        if text and len(text) > 500:
                            text_parts.append(text)
                            break
                    if text_parts:
                        break
            
            # Fallback to general text extraction
            if not text_parts:
                for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    script.decompose()
                text = soup.get_text(separator='\n', strip=True)
                if text and len(text) > 500:
                    text_parts.append(text)
            
            full_text = '\n\n'.join(text_parts)
            return full_text if full_text.strip() else None
            
        except Exception as e:
            print(f"Error downloading Science paper: {e}")
            return None
    
    def download_biorxiv_paper(self) -> Optional[str]:
        """Download and extract text from a bioRxiv paper."""
        try:
            # bioRxiv papers are typically PDFs
            pdf_url = self.url
            if not pdf_url.endswith('.pdf'):
                # Try to convert to PDF URL
                if '/content/' in pdf_url:
                    pdf_url = pdf_url.replace('/content/', '/content/') + '.full.pdf'
                else:
                    pdf_url = pdf_url + '.full.pdf'
            
            print(f"Downloading bioRxiv PDF from: {pdf_url}")
            response = requests.get(pdf_url, timeout=60)
            response.raise_for_status()
            
            # Extract text from PDF
            pdf_file = io.BytesIO(response.content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text_parts = []
            for page in pdf_reader.pages:
                try:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
                except Exception as e:
                    print(f"Error extracting text from page: {e}")
                    continue
            
            full_text = '\n\n'.join(text_parts)
            return full_text if full_text.strip() else None
            
        except Exception as e:
            print(f"Error downloading bioRxiv paper: {e}")
            return None
    
    def download_paper(self) -> Optional[str]:
        """Download the full paper based on its type."""
        if not self.paper_type:
            return None
        
        download_methods = {
            'arxiv': self.download_arxiv_paper,
            'biorxiv': self.download_biorxiv_paper,
            'medrxiv': self.download_biorxiv_paper,  # Same format as bioRxiv
            'nature': self.download_nature_paper,
            'science': self.download_science_paper,
        }
        
        download_method = download_methods.get(self.paper_type)
        if download_method:
            return download_method()
        else:
            # For other publishers, try HTML extraction
            print(f"Attempting HTML extraction for {self.paper_type} paper")
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                response = requests.get(self.url, headers=headers, timeout=60)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    script.decompose()
                
                # Try to find article content
                article = soup.find('article') or soup.find('main')
                if article:
                    text = article.get_text(separator='\n', strip=True)
                else:
                    text = soup.get_text(separator='\n', strip=True)
                
                return text if text and len(text) > 500 else None
            except Exception as e:
                print(f"Error downloading paper from {self.paper_type}: {e}")
                return None

    def extract_paper_sections(self, full_text: str) -> dict:
        """Extract lightweight paper evidence sections for better grounding."""
        text = full_text or ""
        sections = {
            "abstract": "",
            "conclusion": "",
            "figures": [],
            "metrics": [],
        }

        abstract_match = re.search(
            r"\babstract\b\s*(.*?)(?=\b(?:introduction|keywords|1\.?\s+introduction)\b)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if abstract_match:
            sections["abstract"] = _compact_text(abstract_match.group(1))[:4000]

        conclusion_match = re.search(
            r"\b(?:conclusion|conclusions|discussion)\b\s*(.*?)(?=\b(?:references|acknowledg|funding|author contributions)\b)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if conclusion_match:
            sections["conclusion"] = _compact_text(conclusion_match.group(1))[:4000]

        figure_matches = re.findall(
            r"\b(?:fig\.|figure)\s*\d+\.?\s*(.{40,700})",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        sections["figures"] = [_compact_text(match)[:700] for match in figure_matches[:6]]

        metric_sentences = []
        for sentence in re.split(r"(?<=[.!?])\s+", _compact_text(text)):
            lowered = sentence.lower()
            if re.search(r"\d", sentence) and any(
                hint in lowered
                for hint in (
                    "qubit",
                    "fidelity",
                    "error",
                    "threshold",
                    "speedup",
                    "sample",
                    "runtime",
                    "probability",
                    "percent",
                    "%",
                )
            ):
                metric_sentences.append(sentence)
            if len(metric_sentences) >= 10:
                break
        sections["metrics"] = metric_sentences
        return sections

    def build_paper_context(self, title: str, full_text: str) -> str:
        sections = self.extract_paper_sections(full_text)
        parts = [f"Paper Title: {title}"]
        if sections["abstract"]:
            parts.append("Abstract:\n" + sections["abstract"])
        if sections["metrics"]:
            parts.append("Key metric sentences:\n" + "\n".join(f"- {item}" for item in sections["metrics"]))
        if sections["figures"]:
            parts.append("Figure and caption evidence:\n" + "\n".join(f"- {item}" for item in sections["figures"]))
        if sections["conclusion"]:
            parts.append("Conclusion or discussion:\n" + sections["conclusion"])
        parts.append("Full Paper Text:\n" + full_text)
        return "\n\n".join(parts)
    
    def analyze_paper(self, title: str, full_text: str) -> Optional[str]:
        """Generate a concise newsletter summary for a scientific paper."""
        # Truncate text if too long (OpenAI has token limits)
        # Keep first 100k characters and last 50k characters to preserve intro and conclusions
        max_length = 150000
        paper_context = self.build_paper_context(title, full_text)

        if len(paper_context) > max_length:
            first_part = paper_context[:100000]
            last_part = paper_context[-50000:]
            truncated_text = first_part + "\n\n[... content truncated ...]\n\n" + last_part
        else:
            truncated_text = paper_context
        
        prompt = get_prompt("paper.analysis")
        
        try:
            # Use a longer context window model if available
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": truncated_text}
            ]
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=250
            )
            
            analysis = response.choices[0].message.content
            return analysis
            
        except Exception as e:
            print(f"Error analyzing paper with OpenAI: {e}")
            return None
