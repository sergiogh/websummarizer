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
            images = soup.find_all('img', {'src': True})
            for image in images:
                if 'width' in image.attrs:
                    width = image['width']
                    # Assuming the width in pixels if no units are specified
                    if width.endswith('px'):
                        width = width[:-2]
                    try:
                        if int(width) >= 200:
                            # Convert relative image URL to absolute
                            self.image_url = urljoin(self.url, image['src'])
                            break
                    except ValueError:
                        continue
        except requests.exceptions.RequestException as e:
            print(f"Error extracting image from {self.url}: {e}")
            self.image_url = None
