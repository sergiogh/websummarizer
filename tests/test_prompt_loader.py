import unittest

from prompt_loader import get_prompt


class TestPromptLoader(unittest.TestCase):
    def test_newsletter_headline_prompt_has_no_style_guide_chatter(self):
        prompt = get_prompt("newsletter.headline")

        self.assertNotIn("Quantum Pirates Style Guide", prompt)
        self.assertIn("Return only the headline text", prompt)


if __name__ == "__main__":
    unittest.main()
