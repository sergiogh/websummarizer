import unittest

from story_organizer import (
    STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM,
    STORY_BUCKET_INDUSTRY_INVESTMENT,
    STORY_BUCKET_OTHER,
    STORY_BUCKET_POLICY_SECURITY,
    STORY_BUCKET_RESEARCH,
    build_story_digest,
    classify_story_bucket,
    curate_stories,
    order_stories,
    standardize_story_summary,
)


class StoryOrganizerTests(unittest.TestCase):
    def test_papers_are_always_bucketed_as_research(self):
        bucket = classify_story_bucket(
            title="Benchmarking hybrid solver",
            summary="A new study reports wall-clock improvements.",
            url="https://example.com/launch",
            is_paper=True,
        )
        self.assertEqual(bucket, STORY_BUCKET_RESEARCH)

    def test_research_keywords_promote_story_to_research_bucket(self):
        bucket = classify_story_bucket(
            title="University researchers publish new quantum benchmark",
            summary="The paper reports an experimental result on error correction.",
            url="https://news.example.com/story",
        )
        self.assertEqual(bucket, STORY_BUCKET_RESEARCH)

    def test_investment_keywords_map_to_industry_bucket(self):
        bucket = classify_story_bucket(
            title="Startup raises Series A funding for quantum control stack",
            summary="The investment will fund commercial deployment.",
            url="https://company.example.com/news",
        )
        self.assertEqual(bucket, STORY_BUCKET_INDUSTRY_INVESTMENT)

    def test_policy_security_keywords_map_to_policy_bucket(self):
        bucket = classify_story_bucket(
            title="Government issues post-quantum cryptography standards",
            summary="The regulator published a security migration plan.",
            url="https://agency.gov/news",
        )
        self.assertEqual(bucket, STORY_BUCKET_POLICY_SECURITY)

    def test_ecosystem_keywords_map_to_infrastructure_bucket(self):
        bucket = classify_story_bucket(
            title="University launches quantum workforce training hub",
            summary="The new curriculum supports ecosystem readiness.",
            url="https://example.com/education-hub",
        )
        self.assertEqual(bucket, STORY_BUCKET_INFRASTRUCTURE_ECOSYSTEM)

    def test_unmatched_story_falls_back_to_other_bucket(self):
        bucket = classify_story_bucket(
            title="World Quantum Day events announced",
            summary="The roundup covers public outreach activities.",
            url="https://community.example.com/events",
        )
        self.assertEqual(bucket, STORY_BUCKET_OTHER)

    def test_order_stories_groups_buckets_and_keeps_stable_order(self):
        ordered = order_stories(
            [
                {"story_id": "0", "title": "Company raises funding", "summary": "Series A round closes.", "url": "https://company.example.com/funding"},
                {"story_id": "1", "title": "Researchers publish paper", "summary": "University study reports new benchmark.", "url": "https://example.com/paper"},
                {"story_id": "2", "title": "Community event announced", "summary": "Public meetup next week.", "url": "https://example.com/events"},
                {"story_id": "3", "title": "Another paper", "summary": "Scientific benchmark from an institute.", "url": "https://example.com/study"},
            ]
        )

        self.assertEqual([story["story_id"] for story in ordered], ["1", "3", "0", "2"])
        self.assertEqual(ordered[0]["story_bucket"], STORY_BUCKET_RESEARCH)
        self.assertEqual(ordered[2]["story_bucket"], STORY_BUCKET_INDUSTRY_INVESTMENT)
        self.assertEqual(ordered[3]["story_bucket"], STORY_BUCKET_OTHER)

    def test_build_story_digest_uses_ordered_bucket_sequence(self):
        digest = build_story_digest(
            [
                {"story_id": "0", "title": "Funding story", "summary": "Series A funding announced.", "url": "https://company.example.com/funding"},
                {"story_id": "1", "title": "Paper story", "summary": "Researchers at a university publish a paper.", "url": "https://example.com/paper"},
            ]
        )

        self.assertLess(digest.find("[Research & Papers]"), digest.find("[Industry & Investment]"))

    def test_standardize_story_summary_produces_three_required_labels(self):
        standardized = standardize_story_summary(
            "IonQ launched a new system with 64 qubits. The company reported 99.2 percent fidelity. "
            "This enables larger optimization workloads on hardware."
        )

        self.assertIn("What happened:", standardized)
        self.assertIn("Key detail:", standardized)
        self.assertIn("Why this matters:", standardized)

    def test_curate_stories_applies_primary_and_overflow_limits(self):
        stories = []
        for idx in range(30):
            stories.append(
                {
                    "story_id": str(idx),
                    "title": f"Quantum update {idx}",
                    "summary": "A benchmark study reports 95 percent fidelity and a new deployment milestone.",
                    "url": f"https://example.com/2026/03/{idx+1:02d}/story-{idx}",
                    "tag": "research" if idx % 3 == 0 else ("investment" if idx % 3 == 1 else "policy"),
                }
            )

        curated = curate_stories(stories)
        self.assertLessEqual(len(curated["primary"]), 12)
        self.assertLessEqual(len(curated["overflow"]), 8)
        self.assertGreaterEqual(curated["channel_counts"][STORY_BUCKET_RESEARCH], 3)
        self.assertGreaterEqual(curated["channel_counts"][STORY_BUCKET_INDUSTRY_INVESTMENT], 3)
        self.assertGreaterEqual(curated["channel_counts"][STORY_BUCKET_POLICY_SECURITY], 2)


if __name__ == "__main__":
    unittest.main()
