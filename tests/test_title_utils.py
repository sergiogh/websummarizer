import unittest

from title_utils import remove_publisher_mentions, sanitize_generated_headline


class TestTitleUtils(unittest.TestCase):
    def test_sanitize_generated_headline_removes_assistant_intro(self):
        headline = sanitize_generated_headline(
            "Certainly, here is a snappy answer for the title: IBM expands quantum roadmap, IonQ launches new system"
        )

        self.assertEqual(headline, "IBM expands quantum roadmap, IonQ launches new system")

    def test_sanitize_generated_headline_removes_labels_and_quotes(self):
        headline = sanitize_generated_headline(
            'Newsletter headline: "Google publishes error correction result, Quantinuum opens new facility"'
        )

        self.assertEqual(headline, "Google publishes error correction result, Quantinuum opens new facility")

    def test_remove_publisher_mentions_does_not_strip_company_actor(self):
        headline = remove_publisher_mentions(
            "Classiq and Einride demonstrate hybrid quantum-classical optimization workflow improves electric freight dispatch planning",
            "https://classiq.io/news/einride-optimization",
        )

        self.assertEqual(
            headline,
            "Classiq and Einride demonstrate hybrid quantum-classical optimization workflow improves electric freight dispatch planning",
        )

    def test_remove_publisher_mentions_removes_source_suffix(self):
        headline = remove_publisher_mentions(
            "Quantum startup raises seed round - example.com",
            "https://example.com/story",
        )

        self.assertEqual(headline, "Quantum startup raises seed round")


if __name__ == "__main__":
    unittest.main()
