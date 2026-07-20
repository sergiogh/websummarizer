import base64
import unittest
from datetime import datetime
from unittest.mock import patch

import app as app_module


class AppResearchValidationTests(unittest.TestCase):
    @staticmethod
    def _usable_bundle(title):
        source_text = (
            f"{title}. The company described the concrete event, participants, funding, "
            "technical work, location, and implementation plans in detail. "
        ) * 12
        return {
            "raw": source_text,
            "clean": source_text,
            "metadata": {
                "h1": title,
                "extraction_status": "article",
                "clean_text_length": len(source_text),
            },
            "is_paper": False,
            "paper_type": None,
        }

    def test_home_has_no_separate_research_verification_step(self):
        credentials = base64.b64encode(b"admin:test-password").decode("ascii")
        with (
            patch.object(app_module, "BASIC_AUTH_PASSWORD", "test-password"),
            patch.object(app_module, "list_archived_newsletters", return_value=[]),
        ):
            response = app_module.app.test_client().get(
                "/",
                headers={"Authorization": f"Basic {credentials}"},
            )

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Verify selected", html)
        self.assertNotIn("/research/verify", html)
        self.assertIn("Generate Consolidated Newsletter", html)

    def test_candidate_is_rejected_when_fetched_source_date_is_outside_window(self):
        candidate = {
            "title": "QuEra reports a neutral-atom computing milestone",
            "url": "https://example.com/quera-result",
            "published_at": "2026-05-30",
        }
        source_text = (
            "QuEra reports a neutral-atom computing milestone with new hardware results. "
            * 20
        )

        with patch.object(
            app_module,
            "process_url",
            return_value={
                "clean": source_text,
                "metadata": {
                    "published_time": "2026-05-12",
                    "h1": candidate["title"],
                    "extraction_status": "article",
                },
            },
        ):
            result = app_module.verify_research_candidate(
                candidate,
                datetime(2026, 5, 24),
                datetime(2026, 5, 30),
            )

        self.assertEqual(result["status"], "date_mismatch")

    def test_candidate_is_rejected_when_source_content_is_unavailable(self):
        candidate = {
            "title": "Quantum company launches a new control system",
            "url": "https://example.com/unavailable",
            "published_at": "2026-05-28",
        }

        with patch.object(
            app_module,
            "process_url",
            return_value={
                "clean": "",
                "metadata": {"extraction_status": "insufficient_content"},
            },
        ):
            result = app_module.verify_research_candidate(
                candidate,
                datetime(2026, 5, 24),
                datetime(2026, 5, 30),
            )

        self.assertEqual(result["status"], "source_inaccessible")

    def test_research_filter_returns_only_candidates_that_pass_all_checks(self):
        candidates = [
            {"url": "https://example.com/valid", "title": "Valid", "published_at": "2026-05-29"},
            {"url": "https://example.com/old", "title": "Old", "published_at": "2026-05-10"},
            {"url": "https://example.com/blocked", "title": "Blocked", "published_at": "2026-05-29"},
        ]

        def fake_verifier(candidate, _start, _end):
            statuses = {
                "https://example.com/valid": "verified",
                "https://example.com/old": "date_mismatch",
                "https://example.com/blocked": "source_inaccessible",
            }
            return {
                "url": candidate["url"],
                "status": statuses[candidate["url"]],
                "label": "Verified" if statuses[candidate["url"]] == "verified" else "Rejected",
            }

        with (
            patch.object(app_module, "RESEARCH_VALIDATION_WORKERS", 1),
            patch.object(app_module, "verify_research_candidate", side_effect=fake_verifier),
        ):
            verified, filtered_out = app_module.filter_verified_research_candidates(
                candidates,
                datetime(2026, 5, 24),
                datetime(2026, 5, 30),
            )

        self.assertEqual([story["url"] for story in verified], ["https://example.com/valid"])
        self.assertEqual(verified[0]["verification"]["status"], "verified")
        self.assertEqual(filtered_out, 2)

    def test_source_recovery_uses_supporting_url_before_web_discovery(self):
        title = "Photon Queue receives a grant for New Mexico operations"
        supporting_url = "https://example.org/photon-queue-grant"

        def fake_fetch(url, _title):
            if url == supporting_url:
                return self._usable_bundle(title), ""
            return {
                "clean": "Access denied",
                "metadata": {"extraction_status": "insufficient_content"},
            }, ""

        with (
            patch.object(app_module, "_process_url_with_timeout", side_effect=fake_fetch),
            patch.object(app_module, "research_alternate_story_sources") as alternate_search,
        ):
            bundle, resolved_url, attempts = app_module.resolve_story_content(
                title,
                "https://blocked.example.com/story",
                supporting_urls=[supporting_url],
                start_dt=datetime(2026, 7, 14),
                end_dt=datetime(2026, 7, 20),
            )

        self.assertIsNotNone(bundle)
        self.assertEqual(resolved_url, supporting_url)
        self.assertEqual([attempt["kind"] for attempt in attempts], ["primary", "supporting"])
        alternate_search.assert_not_called()

    def test_source_recovery_uses_spreadsheet_summary_before_web_discovery(self):
        title = "Photon Queue receives a grant for New Mexico operations"
        spreadsheet_summary = (
            "Photon Queue received a five hundred thousand dollar grant to establish operations "
            "in Albuquerque, hire locally, lease laboratory space, and assemble and test its "
            "room-temperature quantum memory devices in New Mexico."
        )

        with (
            patch.object(
                app_module,
                "_process_url_with_timeout",
                return_value=(
                    {
                        "clean": "Challenge page",
                        "metadata": {"extraction_status": "insufficient_content"},
                    },
                    "",
                ),
            ),
            patch.object(app_module, "research_alternate_story_sources") as alternate_search,
        ):
            bundle, resolved_url, attempts = app_module.resolve_story_content(
                title,
                "https://blocked.example.com/story",
                spreadsheet_summary=spreadsheet_summary,
                start_dt=datetime(2026, 7, 14),
                end_dt=datetime(2026, 7, 20),
            )

        self.assertEqual(bundle["metadata"]["extraction_status"], "spreadsheet_summary")
        self.assertEqual(resolved_url, "https://blocked.example.com/story")
        self.assertEqual(attempts[-1]["kind"], "spreadsheet_summary")
        alternate_search.assert_not_called()

    def test_source_recovery_uses_exact_event_web_alternate_last(self):
        title = "Photon Queue receives a grant for New Mexico operations"
        alternate_url = "https://news.example.org/photon-queue-new-mexico"

        def fake_fetch(url, _title):
            if url == alternate_url:
                return self._usable_bundle(title), ""
            return {
                "clean": "Challenge page",
                "metadata": {"extraction_status": "insufficient_content"},
            }, ""

        with (
            patch.object(app_module, "_process_url_with_timeout", side_effect=fake_fetch),
            patch.object(
                app_module,
                "research_alternate_story_sources",
                return_value=[{"url": alternate_url, "published_at": "2026-07-15"}],
            ),
        ):
            bundle, resolved_url, attempts = app_module.resolve_story_content(
                title,
                "https://blocked.example.com/story",
                start_dt=datetime(2026, 7, 14),
                end_dt=datetime(2026, 7, 20),
            )

        self.assertIsNotNone(bundle)
        self.assertEqual(resolved_url, alternate_url)
        self.assertEqual(attempts[-1]["kind"], "discovered_alternate")

    def test_exhausted_recovery_is_marked_unparseable_and_partitioned_out(self):
        title = "Photon Queue receives a grant for New Mexico operations"
        with (
            patch.object(
                app_module,
                "_process_url_with_timeout",
                return_value=(
                    {
                        "clean": "Challenge page",
                        "metadata": {"extraction_status": "insufficient_content"},
                    },
                    "",
                ),
            ),
            patch.object(app_module, "research_alternate_story_sources", return_value=[]),
        ):
            story = app_module.process_story(
                3,
                "https://blocked.example.com/story",
                title,
                "industry",
                published_at="2026-07-15",
                start_dt=datetime(2026, 7, 14),
                end_dt=datetime(2026, 7, 20),
            )

        self.assertEqual(story["status"], "unparseable")
        self.assertEqual(story["summary"], "")
        self.assertIn("unparseable_source", story["qa_flags"])

        publishable, unparseable = app_module.partition_publishable_stories(
            [
                story,
                {
                    "story_id": "ok",
                    "title": "Usable story",
                    "summary": "A complete grounded story summary.",
                    "qa_flags": [],
                },
            ]
        )
        self.assertEqual([item["story_id"] for item in publishable], ["ok"])
        self.assertEqual([item["story_id"] for item in unparseable], ["3"])

    def test_finalize_omits_unparseable_story_from_newsletter_html(self):
        unparseable_story = app_module.build_unparseable_story(
            3,
            "https://blocked.example.com/story",
            "Blocked operational diagnostic",
            "industry",
            reason="All source recovery options failed.",
        )
        good_story = {
            "story_id": "ok",
            "url": "https://example.com/usable",
            "title": "Usable quantum story",
            "summary": (
                "A research team reported a usable quantum result with supporting technical "
                "evidence and a clearly described implementation path."
            ),
            "tag": "research",
            "qa_flags": [],
        }

        with (
            patch.object(
                app_module,
                "build_aggregate_outputs",
                return_value={
                    "global_summary": "One usable story was published.",
                    "micro_summary": "Usable weekly quantum update",
                    "aggregate_qa": {},
                    "passed_results": [good_story],
                },
            ),
            patch.object(app_module, "generate_cover_image_data_url", return_value=""),
            patch.object(app_module, "fetch_latest_quantum_bits_comic", return_value=None),
        ):
            newsletter = app_module.finalize_newsletter(
                [good_story, unparseable_story]
            )

        self.assertEqual(newsletter["unparseable_story_count"], 1)
        self.assertIn("Usable quantum story", newsletter["html"])
        self.assertNotIn("Blocked operational diagnostic", newsletter["html"])
        self.assertNotIn("All source recovery options failed", newsletter["html"])


if __name__ == "__main__":
    unittest.main()
