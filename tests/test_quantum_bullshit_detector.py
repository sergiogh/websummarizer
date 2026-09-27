import unittest
from datetime import datetime, timezone

from quantum_bullshit_detector import (
    DIMENSIONS,
    assess_quantum_claim,
    is_detector_candidate,
    render_detector_badge,
)


class QuantumBullshitDetectorTests(unittest.TestCase):
    def test_funding_and_government_grants_are_not_candidates(self):
        for title in (
            "Quantum startup raises $25 million Series A",
            "Government grant awarded to regional quantum hub",
        ):
            self.assertFalse(
                is_detector_candidate(
                    title,
                    "The money will support future research and hiring.",
                    "The organization works on quantum computers and qubits.",
                )
            )

    def test_consequential_technical_claim_is_a_candidate(self):
        self.assertTrue(
            is_detector_candidate(
                "Company demonstrates error-corrected logical qubits",
                "The experiment reports a below-threshold logical error rate.",
                "The team measured the encoded circuit across several code distances.",
            )
        )

    def test_unfamiliar_claim_wording_reaches_semantic_eligibility_check(self):
        self.assertTrue(
            is_detector_candidate(
                "QubitWorks cuts calibration time tenfold",
                "The company reports a new control-stack result.",
                "A technical description follows.",
            )
        )

    def test_assessment_calculates_score_and_timestamp_from_twelve_dimensions(self):
        scores = [0, 1, 2, 1, 0, 2, 1, 2, 1, 2, 1, 0]
        response = {
            "eligible": True,
            "eligibility_reason": "A new logical-qubit result could alter the fault-tolerance roadmap.",
            "claim_type": "company_claim",
            "primary_claim": "The encoded qubit remains below threshold as code distance increases.",
            "verdict": "The experiment is concrete, but scaling and overhead remain uncertain.",
            "dimensions": [
                {"id": dimension_id, "score": score, "reason": "Evidence-based reason."}
                for (dimension_id, _), score in zip(DIMENSIONS, scores)
            ],
        }

        assessment = assess_quantum_claim(
            "Company demonstrates below-threshold error correction",
            "The company reports a new logical-qubit experiment.",
            "Detailed logical error-rate measurements and scaling claims.",
            assessed_at=datetime(2026, 9, 27, 12, 30, tzinfo=timezone.utc),
            analyzer=lambda _prompt, _payload: response,
        )

        self.assertIsNotNone(assessment)
        self.assertEqual(assessment["raw_score"], sum(scores))
        self.assertEqual(assessment["risk_score"], round(sum(scores) / 24 * 100))
        self.assertEqual(assessment["assessed_at"], "2026-09-27T12:30:00Z")
        self.assertEqual(len(assessment["dimensions"]), 12)

    def test_ineligible_model_decision_is_not_rendered(self):
        assessment = assess_quantum_claim(
            "Company launches a new quantum computer",
            "The launch announcement contains no new measured result.",
            "The product announcement describes a quantum computer.",
            analyzer=lambda _prompt, _payload: {
                "eligible": False,
                "claim_type": "not_eligible",
                "dimensions": [],
            },
        )
        self.assertIsNone(assessment)
        self.assertEqual(render_detector_badge(assessment), "")

    def test_incomplete_dimension_profile_is_rejected(self):
        assessment = assess_quantum_claim(
            "Researchers report a quantum advantage benchmark",
            "A new study reports a benchmark result.",
            "The paper describes a quantum advantage experiment.",
            analyzer=lambda _prompt, _payload: {
                "eligible": True,
                "dimensions": [
                    {"id": DIMENSIONS[0][0], "score": 1, "reason": "Partial."}
                ],
            },
        )
        self.assertIsNone(assessment)

    def test_badge_shows_score_and_visible_snapshot_time(self):
        assessment = {
            "eligible": True,
            "risk_score": 71,
            "assessed_at": "2026-09-27T12:30:00Z",
            "verdict": "Evidence is narrow.",
            "dimensions": [],
        }
        badge = render_detector_badge(assessment)
        self.assertIn("Quantum BS-risk 71/100", badge)
        self.assertIn("assessed 2026-09-27 12:30:00 UTC", badge)
        self.assertIn('datetime="2026-09-27T12:30:00Z"', badge)


if __name__ == "__main__":
    unittest.main()
