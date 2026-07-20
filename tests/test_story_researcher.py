import unittest
from datetime import datetime

from story_researcher import (
    build_alternate_source_prompt,
    build_research_prompt,
    clamp_research_window,
    extract_response_text,
    is_date_in_window,
    normalize_research_candidates,
    parse_candidate_date,
    parse_research_response,
    research_alternate_story_sources,
    research_quantum_stories,
    title_signature,
)


class StoryResearcherTests(unittest.TestCase):
    def test_parse_research_response_accepts_fenced_json(self):
        payload = """```json
        {
          "candidates": [
            {
              "title": "Quantum lab reports new qubit result",
              "url": "https://example.com/quantum-result",
              "publisher": "Example",
              "tag": "research"
            }
          ]
        }
        ```"""

        candidates = parse_research_response(payload)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://example.com/quantum-result")

    def test_normalize_candidates_drops_missing_urls_and_flags_duplicates(self):
        existing = [
            {
                "title_seed": "IonQ launches new quantum system",
                "url": "https://example.com/ionq",
            }
        ]
        raw = [
            {"title": "Missing URL", "publisher": "Example"},
            {
                "title": "IonQ launches new quantum system",
                "url": "https://example.com/ionq/",
                "publisher": "Example",
                "tag": "industry",
            },
            {
                "title": "New quantum processor paper",
                "url": "https://example.org/paper",
                "publisher": "Example Journal",
                "tag": "research",
                "confidence": "0.8",
            },
        ]

        candidates = normalize_research_candidates(raw, existing)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["duplicate_of"], "IonQ launches new quantum system")
        self.assertIsNone(candidates[1]["duplicate_of"])
        self.assertEqual(candidates[1]["confidence"], 0.8)

    def test_normalize_candidates_filters_outside_date_window(self):
        raw = [
            {
                "title": "Before window",
                "url": "https://example.com/before",
                "published_at": "2026-05-23",
            },
            {
                "title": "Inside window",
                "url": "https://example.com/inside",
                "published_at": "May 30, 2026",
            },
            {
                "title": "After window",
                "url": "https://example.com/after",
                "published_at": "2026-05-31",
            },
            {
                "title": "Unknown date",
                "url": "https://example.com/unknown",
            },
        ]

        candidates = normalize_research_candidates(
            raw,
            start_date=datetime(2026, 5, 24),
            end_date=datetime(2026, 5, 30),
        )

        self.assertEqual([candidate["title"] for candidate in candidates], ["Inside window"])
        self.assertEqual(candidates[0]["published_at"], "2026-05-30")

    def test_date_window_is_inclusive(self):
        self.assertTrue(
            is_date_in_window("2026-05-24", datetime(2026, 5, 24), datetime(2026, 5, 30))
        )
        self.assertTrue(
            is_date_in_window("2026-05-30", datetime(2026, 5, 24), datetime(2026, 5, 30))
        )
        self.assertFalse(
            is_date_in_window("2026-05-31", datetime(2026, 5, 24), datetime(2026, 5, 30))
        )
        self.assertIsNone(parse_candidate_date("last week"))

    def test_build_research_prompt_includes_date_window_and_existing_stories(self):
        prompt = build_research_prompt(
            datetime(2026, 5, 24),
            datetime(2026, 5, 30),
            [{"title_seed": "Already covered", "url": "https://example.com/covered"}],
            limit=12,
        )

        self.assertIn("DATE_WINDOW_START: 2026-05-24", prompt)
        self.assertIn("DATE_WINDOW_END: 2026-05-30", prompt)
        self.assertIn("MAX_CANDIDATES: 12", prompt)
        self.assertIn("Already covered | https://example.com/covered", prompt)
        self.assertIn("at most seven calendar days", prompt)
        self.assertIn("confirm that the dated article", prompt)
        self.assertIn("inaccessible sources", prompt)

    def test_alternate_source_prompt_requires_exact_accessible_event_coverage(self):
        prompt = build_alternate_source_prompt(
            "Photon Queue receives a grant for New Mexico operations",
            "https://blocked.example.com/photon-queue",
            datetime(2026, 7, 14),
            datetime(2026, 7, 20),
            limit=3,
        )

        self.assertIn("exact same news event", prompt)
        self.assertIn("ORIGINAL_URL: https://blocked.example.com/photon-queue", prompt)
        self.assertIn("DATE_WINDOW_START: 2026-07-14", prompt)
        self.assertIn("MAX_CANDIDATES: 3", prompt)
        self.assertIn("Exclude the original URL", prompt)

    def test_alternate_source_provider_receives_story_and_date_window(self):
        class RecordingProvider:
            def __init__(self):
                self.request = None

            def search(self, title, original_url, start_date, end_date, limit):
                self.request = (title, original_url, start_date, end_date, limit)
                return [{"url": "https://example.com/alternate"}]

        provider = RecordingProvider()
        candidates = research_alternate_story_sources(
            "Photon Queue grant",
            "https://blocked.example.com/story",
            datetime(2026, 7, 14),
            datetime(2026, 7, 20),
            limit=2,
            provider=provider,
        )

        self.assertEqual(candidates[0]["url"], "https://example.com/alternate")
        self.assertEqual(provider.request[0], "Photon Queue grant")
        self.assertEqual(provider.request[4], 2)

    def test_research_window_is_capped_to_final_seven_calendar_days(self):
        start, end = clamp_research_window(
            datetime(2026, 5, 1),
            datetime(2026, 5, 30, 23, 59, 59),
        )

        self.assertEqual(start.strftime("%Y-%m-%d"), "2026-05-24")
        self.assertEqual(end.strftime("%Y-%m-%d"), "2026-05-30")

    def test_research_provider_never_receives_more_than_seven_days(self):
        class RecordingProvider:
            def __init__(self):
                self.window = None

            def search(self, start_date, end_date, existing_stories, limit):
                self.window = (start_date, end_date)
                return []

        provider = RecordingProvider()
        research_quantum_stories(
            datetime(2026, 5, 1),
            datetime(2026, 5, 30, 23, 59, 59),
            [],
            provider=provider,
        )

        self.assertEqual(provider.window[0].strftime("%Y-%m-%d"), "2026-05-24")
        self.assertEqual(provider.window[1].strftime("%Y-%m-%d"), "2026-05-30")

    def test_extract_response_text_reads_responses_output_shape(self):
        text = extract_response_text(
            {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "{\"candidates\": []}"}
                        ]
                    }
                ]
            }
        )

        self.assertEqual(text, "{\"candidates\": []}")

    def test_title_signature_normalizes_common_words(self):
        self.assertEqual(
            title_signature("The Quantum Result for a New Processor"),
            "quantum result new processor",
        )


if __name__ == "__main__":
    unittest.main()
