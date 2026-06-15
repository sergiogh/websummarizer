import unittest

from qa_checks import validate_aggregate_grounding, validate_story_grounding, validate_summary_claims
from story_grounding import (
    build_extractive_fallback_summary,
    build_safe_summary_fallback,
    failure_summary,
    filter_passed_stories,
    sanitize_story_summary_text,
)
from url_processor import extract_article_payload


class SourceGroundingTests(unittest.TestCase):
    def test_article_extraction_prefers_article_body_over_related_links(self):
        html = """
        <html>
          <head>
            <title>IonQ launches new system</title>
            <meta property="og:title" content="IonQ launches new quantum system">
            <meta property="article:published_time" content="2026-05-30T08:00:00Z">
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
        self.assertEqual(extraction["metadata"]["published_time"], "2026-05-30T08:00:00Z")

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

    def test_grounding_does_not_treat_headline_case_as_missing_entity_when_source_matches(self):
        source = (
            "Nokia has expanded its work in artificial intelligence-driven networking and quantum security. "
            "The company introduced Deepfield Genome Shield for telecom and cloud networks, partnered with "
            "Quantropi on quantum-safe key distribution, and is working with Indosat Ooredoo Hutchison and "
            "NVIDIA to modernize Indonesia's 5G networks."
        )
        metadata = {
            "html_title": "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership",
            "h1": "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership",
            "extraction_status": "article",
        }

        result = validate_story_grounding(
            "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership",
            "Nokia has expanded its work in artificial intelligence-driven networking and quantum security.",
            source,
            metadata,
        )

        self.assertNotIn("title_entities_not_in_source", result["flags"])

    def test_failure_summary_mentions_manual_review_reason(self):
        message = failure_summary("source_mismatch", ["source_title_mismatch"])
        self.assertIn("does not clearly match", message)

    def test_extractive_fallback_reuses_source_sentences(self):
        source = (
            "IonQ announced a new quantum computing system for enterprise users. "
            "The company said the launch expands access for existing customers. "
            "The system includes 64 qubits and targets production workloads. "
            "Related article: a networking company announced a classical switch. "
            "IonQ said the release is intended for customers testing larger circuits."
        )

        fallback = build_extractive_fallback_summary(
            "IonQ launches new quantum system",
            source,
            {"html_title": "IonQ launches new quantum system", "h1": "IonQ launches new quantum system"},
        )

        self.assertEqual(fallback["status"], "extractive_fallback")
        self.assertIn("IonQ announced", fallback["summary"])
        self.assertIn("64 qubits", fallback["summary"])
        self.assertNotIn("Related article", fallback["summary"])

    def test_summary_sanitizer_removes_market_preamble_and_keeps_story_paragraph(self):
        dirty = (
            "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership "
            "Simply Wall St Fri, June 12, 2026 at 1:12 AM EDT 4 min read NOKIA.HE NVDA NOK "
            "Find winning stocks in any market cycle. "
            "Nokia is working with Indosat Ooredoo Hutchison and NVIDIA to modernize "
            "Indonesia's 5G networks using AI centric technologies. "
            "The company has partnered with Quantropi to work on carrier grade, "
            "quantum safe key distribution for future resistant networking."
        )

        result = sanitize_story_summary_text(
            dirty,
            "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership",
        )

        cleaned = result["summary"]
        self.assertNotIn("Simply Wall St", cleaned)
        self.assertNotIn("4 min read", cleaned)
        self.assertNotIn("NOKIA.HE", cleaned)
        self.assertNotIn("Find winning stocks", cleaned)
        self.assertIn("5G networks", cleaned)
        self.assertIn("AI centric technologies", cleaned)
        self.assertTrue(cleaned.startswith("Nokia is working"))

    def test_summary_sanitizer_removes_press_release_dateline(self):
        dirty = (
            "BOULDER, Colo., June 15, 2026 /PRNewswire/ -- Atom Computing announced "
            "a new neutral atom quantum computing system for commercial users. "
            "The company said the system is designed to support larger experiments "
            "and improve access for enterprise research teams."
        )

        result = sanitize_story_summary_text(
            dirty,
            "Atom Computing announced a new quantum computing system",
        )

        cleaned = result["summary"]
        self.assertNotIn("BOULDER", cleaned)
        self.assertNotIn("PRNewswire", cleaned)
        self.assertTrue(cleaned.startswith("Atom Computing announced"))
        self.assertIn("enterprise research teams", cleaned)

    def test_summary_sanitizer_removes_fragments_without_dropping_story(self):
        dirty = (
            "Latest update. Quantum Machines introduced a control platform for quantum processors. "
            "The company said the platform helps laboratories coordinate calibration workflows."
        )

        result = sanitize_story_summary_text(
            dirty,
            "Quantum Machines introduced a control platform",
        )

        cleaned = result["summary"]
        self.assertNotIn("Latest update", cleaned)
        self.assertTrue(cleaned.startswith("Quantum Machines introduced"))

    def test_extractive_fallback_skips_market_metadata_preamble(self):
        source = (
            "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership "
            "Simply Wall St Fri, June 12, 2026 at 1:12 AM EDT 4 min read NOKIA.HE NVDA NOK "
            "Find winning stocks in any market cycle. "
            "Nokia is working with Indosat Ooredoo Hutchison and NVIDIA to modernize "
            "Indonesia's 5G networks using AI centric technologies. "
            "The company has partnered with Quantropi to work on carrier grade, "
            "quantum safe key distribution for future resistant networking. "
            "The work is positioned as part of Nokia's broader push to apply security and "
            "automation technologies to communications infrastructure."
        )

        fallback = build_extractive_fallback_summary(
            "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership",
            source,
            {
                "html_title": "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership",
                "h1": "Nokia Extends AI Networking And Quantum Security Push With 5G Partnership",
            },
        )

        self.assertEqual(fallback["status"], "extractive_fallback")
        self.assertNotIn("Simply Wall St", fallback["summary"])
        self.assertNotIn("NOKIA.HE", fallback["summary"])
        self.assertIn("Nokia is working", fallback["summary"])

    def test_safe_fallback_repairs_summary_claim_failure(self):
        source = (
            "Quantum Machines introduced a control platform for quantum processors. "
            "The company said the platform helps laboratories coordinate calibration and control workflows. "
            "It includes new orchestration software for experiments across multiple hardware backends. "
            "Quantum Machines said the release is aimed at research teams moving from prototypes to repeatable operations."
        )

        fallback = build_safe_summary_fallback(
            "Quantum Machines introduces control platform",
            source,
            {"html_title": "Quantum Machines introduces control platform", "h1": "Quantum Machines introduces control platform"},
            ["summary_claims_not_supported", "summary_entities_not_in_source"],
        )

        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["remaining_flags"], [])
        self.assertNotIn("Unable to generate", fallback["summary"])

    def test_safe_fallback_refuses_source_mismatch(self):
        source = (
            "WrongCo announced a networking product for data centers. "
            "The article focuses on Ethernet switching and rack-scale cloud infrastructure. "
            "WrongCo said the release targets lower latency operations for enterprise customers."
        )

        fallback = build_safe_summary_fallback(
            "IonQ launches new quantum system",
            source,
            {"html_title": "WrongCo announces networking product", "h1": "WrongCo announces networking product"},
            ["source_title_mismatch"],
        )

        self.assertIsNone(fallback)

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
