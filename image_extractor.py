import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import Optional


class ImageExtractor:
    def __init__(self, url: str):
        self.url: str = url
        self.image_url: Optional[str] = None

    def extract_image(self) -> None:
        """Extract the most relevant image from the URL."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'}
            response = requests.get(self.url, headers=headers, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Prefer article metadata images first
            selectors = [
                ("meta", {"property": "og:image"}),
                ("meta", {"name": "twitter:image"}),
                ("meta", {"property": "twitter:image"}),
            ]
            for tag, attrs in selectors:
                node = soup.find(tag, attrs=attrs)
                if node is None:
                    continue
                candidate = (node.get("content") or "").strip()
                if candidate:
                    self.image_url = urljoin(self.url, candidate)
                    return

            best_url = None
            best_area = 0
            images = soup.find_all('img', {'src': True})
            for image in images:
                src = (image.get("src") or "").strip()
                if not src:
                    continue

                width_raw = str(image.get('width', '')).replace("px", "").strip()
                height_raw = str(image.get('height', '')).replace("px", "").strip()
                try:
                    width = int(width_raw) if width_raw else 0
                    height = int(height_raw) if height_raw else 0
                except ValueError:
                    width = 0
                    height = 0

                area = width * height if width and height else width
                if area > best_area and width >= 200:
                    best_area = area
                    best_url = urljoin(self.url, src)

            if best_url:
                self.image_url = best_url
                return

            # Final fallback to first image
            for image in images:
                src = (image.get("src") or "").strip()
                if src:
                    self.image_url = urljoin(self.url, src)
                    return
        except requests.exceptions.RequestException as e:
            print(f"Error extracting image from {self.url}: {e}")
            self.image_url = None
