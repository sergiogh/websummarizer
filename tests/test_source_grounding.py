import unittest

from qa_checks import validate_aggregate_grounding, validate_story_grounding, validate_summary_claims
from story_grounding import failure_summary, filter_passed_stories
from url_processor import extract_article_payload


class SourceGroundingTests(unittest.TestCase):
    def test_article_extraction_prefers_article_body_over_related_links(self):
        html = """
        <html>
          <head>
            <title>IonQ launches new system</title>
            <meta property="og:title" content="IonQ launches new quantum system">
          </head>
          <body>
            <nav>Latest: WrongCo raises $900 million for unrelated chips</nav>
            <article>
              <h1>IonQ launches new quantum system</h1>
              <p>IonQ announced a new quantum computing system for enterprise users.</p>
              <p>The system includes 64 qubits and targets production workloads.</p>
              <p>The company said the launch expands access for existing customers.</p>
            </article>
            <aside>Related article: WrongCo builds a photonics factory.</aside>
          </body>
        </html>
        """

        extraction = extract_article_payload(html, "https://example.com/ionq")

        self.assertIn("IonQ announced", extraction["text"])
        self.assertNotIn("WrongCo raises", extraction["text"])
        self.assertEqual(extraction["metadata"]["h1"], "IonQ launches new quantum system")

    def test_grounding_flags_wrong_downloaded_story(self):
        source = (
            "WrongCo announced a classical networking product for data centers. "
            "The article focuses on Ethernet switching, optical transport, and rack-scale "
            "network management for cloud infrastructure customers. WrongCo said the release "
            "targets lower latency operations for enterprise data centers. The announcement "
            "describes port density, software management, and customer support services, "
            "with no discussion of the selected company's hardware roadmap or computing launch."
        )
        metadata = {
            "html_title": "WrongCo announces networking product",
            "h1": "WrongCo announces networking product",
            "extraction_status": "article",
        }

        result = validate_story_grounding(
            "IonQ launches new quantum system",
            "IonQ launched a 64-qubit quantum system for enterprise users.",
            source,
            metadata,
        )

        self.assertFalse(result["passed"])
        self.assertIn("source_title_mismatch", result["flags"])

    def test_grounding_flags_summary_numbers_missing_from_source(self):
        source = (
            "IonQ announced a new quantum computing system for enterprise users. "
            "The company said the launch expands access for existing customers."
        )
        metadata = {
            "html_title": "IonQ launches new quantum system",
            "h1": "IonQ launches new quantum system",
            "extraction_status": "article",
        }

        result = validate_story_grounding(
            "IonQ launches new quantum system",
            "IonQ launched a 64-qubit quantum system for enterprise users.",
            source,
            metadata,
        )

        self.assertFalse(result["passed"])
        self.assertIn("summary_numbers_not_in_source", result["flags"])
        self.assertIn("64", result["missing_numbers"])

    def test_failure_summary_mentions_manual_review_reason(self):
        message = failure_summary("source_mismatch", ["source_title_mismatch"])
        self.assertIn("does not clearly match", message)

    def test_filter_passed_stories_excludes_flagged_items(self):
        stories = [
            {"story_id": "1", "title": "Good", "summary": "Good source.", "qa_flags": []},
            {"story_id": "2", "title": "Bad", "summary": "Bad source.", "qa_flags": ["source_title_mismatch"]},
            {"story_id": "3", "title": "Failed", "summary": "Failed.", "grounding": {"passed": False}},
        ]

        passed = filter_passed_stories(stories)

        self.assertEqual([story["story_id"] for story in passed], ["1"])

    def test_claim_check_flags_unsupported_number(self):
        source = "IonQ announced a new quantum computing system for enterprise users."
        result = validate_summary_claims(
            "IonQ announced a 64-qubit quantum computing system.",
            source,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["unsupported_claims"][0]["missing_numbers"], ["64"])

    def test_aggregate_validation_flags_entities_not_in_passed_stories(self):
        result = validate_aggregate_grounding(
            "IonQ announced a new system while WrongCo raised funding.",
            [{"title": "IonQ announces new system", "summary": "IonQ announced a new system.", "url": "https://example.com"}],
        )

        self.assertFalse(result["passed"])
        self.assertIn("aggregate_entities_not_in_passed_stories", result["flags"])


if __name__ == "__main__":
    unittest.main()
