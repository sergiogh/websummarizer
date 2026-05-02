import requests
from bs4 import BeautifulSoup
from typing import Optional
import time
import random
import urllib3
from urllib.parse import urlparse, urljoin
import json

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class AlternativeFetcher:
    """Alternative content fetcher with different strategies for problematic URLs."""
    
    def __init__(self, url: str):
        self.url = url
        self.content: Optional[str] = None
        
    def fetch_with_selenium_fallback(self) -> bool:
        """Try to fetch content using requests first, then suggest selenium if available."""
        try:
            # First try with requests
            return self._fetch_with_requests()
        except Exception as e:
            print(f"Requests failed for {self.url}: {e}")
            print("Consider using selenium for JavaScript-heavy sites")
            return False
    
    def _fetch_with_requests(self) -> bool:
        """Fetch content using requests with multiple strategies."""
        strategies = [
            self._strategy_curl_like,
            self._strategy_mobile_headers,
            self._strategy_api_headers,
            self._strategy_minimal_headers
        ]
        
        for i, strategy in enumerate(strategies):
            try:
                print(f"Trying alternative strategy {i+1} for {self.url}")
                if strategy():
                    print(f"Successfully fetched content using alternative strategy {i+1}")
                    return True
            except Exception as e:
                print(f"Alternative strategy {i+1} failed: {e}")
                continue
        
        return False
    
    def _strategy_curl_like(self) -> bool:
        """Strategy that mimics curl behavior."""
        headers = {
            'User-Agent': 'curl/7.68.0',
            'Accept': '*/*',
            'Connection': 'close'
        }
        
        response = requests.get(
            self.url,
            headers=headers,
            timeout=20,
            verify=False,
            allow_redirects=True
        )
        response.raise_for_status()
        self.content = response.text
        return True
    
    def _strategy_mobile_headers(self) -> bool:
        """Strategy using mobile headers."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        response = requests.get(
            self.url,
            headers=headers,
            timeout=25,
            verify=True,
            allow_redirects=True
        )
        response.raise_for_status()
        self.content = response.text
        return True
    
    def _strategy_api_headers(self) -> bool:
        """Strategy using API-like headers."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; NewsBot/1.0)',
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        
        response = requests.get(
            self.url,
            headers=headers,
            timeout=30,
            verify=True,
            allow_redirects=True
        )
        response.raise_for_status()
        self.content = response.text
        return True
    
    def _strategy_minimal_headers(self) -> bool:
        """Strategy with minimal headers."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; MSIE 10.0; Windows NT 6.1; Trident/6.0)'
        }
        
        response = requests.get(
            self.url,
            headers=headers,
            timeout=35,
            verify=False,
            allow_redirects=True
        )
        response.raise_for_status()
        self.content = response.text
        return True
    
    def get_content(self) -> Optional[str]:
        """Get the fetched content."""
        return self.content
    
    def clean_content(self) -> None:
        """Clean the HTML content."""
        if self.content:
            soup = BeautifulSoup(self.content, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                element.decompose()
            
            # Get text content
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            self.content = ' '.join(chunk for chunk in chunks if chunk)

