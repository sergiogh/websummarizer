import requests
from bs4 import BeautifulSoup
from typing import Optional

class UrlProcessor:
    def __init__(self, url: str):
        self.url: str = url
        self.content: Optional[str] = None

    def download_content(self) -> None:
        """Download the content of the URL."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'}
            response = requests.get(self.url, headers=headers, timeout=20)
            response.raise_for_status()
            self.content = response.text
        except requests.exceptions.RequestException as e:
            print(f"Error downloading content from {self.url}: {e}")
            self.content = None

    def strip_html(self) -> None:
        """Strip the HTML tags from the content."""
        if self.content is not None:
            soup = BeautifulSoup(self.content, 'html.parser')
            self.content = soup.get_text(strip=True)
        else:
            print(f"No content to strip HTML from for {self.url}")
