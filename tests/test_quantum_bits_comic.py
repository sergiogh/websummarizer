import os
import tempfile
import unittest

from quantum_bits_comic import fetch_latest_quantum_bits_comic


class FakeResponse:
    def __init__(self, text="", json_data=None, content=b"", headers=None, url="https://example.com"):
        self.text = text
        self._json_data = json_data
        self.content = content
        self.headers = headers or {}
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_data


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        response = self.responses.get(url)
        if response is None:
            raise AssertionError(f"Unexpected URL requested: {url}")
        return response


class QuantumBitsComicTests(unittest.TestCase):
    def test_fetches_latest_comic_from_rss_and_downloads_image(self):
        feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Older Strip</title>
      <link>https://quantumbitscomics.com/older-strip/</link>
      <pubDate>Sun, 22 Feb 2026 08:00:27 +0000</pubDate>
      <description><![CDATA[
        <img src="https://quantumbitscomics.com/wp-content/uploads/2026/02/older.jpg" />
        Older summary
      ]]></description>
    </item>
    <item>
      <title>Qubit &#8211; A Quantum Bit</title>
      <link>https://quantumbitscomics.com/qubit-a-quantum-bit/</link>
      <pubDate>Sun, 01 Mar 2026 14:27:00 +0000</pubDate>
      <description><![CDATA[
        <img src="https://quantumbitscomics.com/wp-content/uploads/2026/02/qubit.jpg" />
        A regular computer stores everything as bits.
        <a href="https://quantumbitscomics.com/qubit-a-quantum-bit/">Read More</a>
      ]]></description>
    </item>
  </channel>
</rss>
"""
        session = FakeSession(
            {
                "https://quantumbitscomics.com/feed/": FakeResponse(text=feed_xml),
                "https://quantumbitscomics.com/wp-content/uploads/2026/02/qubit.jpg": FakeResponse(
                    content=b"fake-image-bytes",
                    headers={"Content-Type": "image/jpeg"}
                ),
            }
        )

        with tempfile.TemporaryDirectory() as run_dir:
            comic = fetch_latest_quantum_bits_comic(run_dir, session=session)

            self.assertIsNotNone(comic)
            self.assertEqual(comic["title"], "Qubit – A Quantum Bit")
            self.assertEqual(comic["source"], "rss")
            self.assertIn("A regular computer stores everything as bits.", comic["summary"])
            self.assertNotIn("Read More", comic["summary"])
            self.assertTrue(comic["image_filename"].startswith("quantum_bits_latest"))
            self.assertTrue(os.path.exists(os.path.join(run_dir, comic["image_filename"])))

    def test_enriches_rss_item_from_wordpress_when_feed_description_is_missing_fields(self):
        feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Qubit &#8211; A Quantum Bit</title>
      <link>https://quantumbitscomics.com/qubit-a-quantum-bit/</link>
      <pubDate>Sun, 01 Mar 2026 14:27:00 +0000</pubDate>
      <description><![CDATA[No image here]]></description>
    </item>
  </channel>
</rss>
"""
        wp_url = "https://quantumbitscomics.com/wp-json/wp/v2/posts?slug=qubit-a-quantum-bit&_embed=1"
        session = FakeSession(
            {
                "https://quantumbitscomics.com/feed/": FakeResponse(text=feed_xml),
                wp_url: FakeResponse(
                    json_data=[
                        {
                            "title": {"rendered": "Qubit &#8211; A Quantum Bit"},
                            "link": "https://quantumbitscomics.com/qubit-a-quantum-bit/",
                            "date_gmt": "2026-03-01T14:27:00",
                            "content": {
                                "rendered": """
                                    <figure><img src="https://quantumbitscomics.com/wp-content/uploads/2026/02/qubit.jpg" /></figure>
                                    <p>A qubit can be tuned so that measurement returns 0 or 1 with different probabilities.</p>
                                """
                            },
                            "excerpt": {"rendered": "<p>Excerpt text</p>"},
                            "_embedded": {
                                "wp:featuredmedia": [
                                    {
                                        "source_url": "https://quantumbitscomics.com/wp-content/uploads/2026/02/qubit.jpg",
                                        "media_details": {
                                            "sizes": {
                                                "full": {
                                                    "source_url": "https://quantumbitscomics.com/wp-content/uploads/2026/02/qubit.jpg"
                                                }
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    ],
                    url=wp_url
                ),
                "https://quantumbitscomics.com/wp-content/uploads/2026/02/qubit.jpg": FakeResponse(
                    content=b"fake-image-bytes",
                    headers={"Content-Type": "image/jpeg"}
                ),
            }
        )

        with tempfile.TemporaryDirectory() as run_dir:
            comic = fetch_latest_quantum_bits_comic(run_dir, session=session)

            self.assertIsNotNone(comic)
            self.assertEqual(comic["title"], "Qubit – A Quantum Bit")
            self.assertEqual(comic["source"], "rss")
            self.assertEqual(comic["api_url"], wp_url)
            self.assertEqual(
                comic["summary"],
                "A qubit can be tuned so that measurement returns 0 or 1 with different probabilities."
            )
            self.assertTrue(os.path.exists(os.path.join(run_dir, comic["image_filename"])))


if __name__ == "__main__":
    unittest.main()
