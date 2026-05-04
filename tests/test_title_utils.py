import unittest

from title_utils import sanitize_generated_headline


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


if __name__ == "__main__":
    unittest.main()
