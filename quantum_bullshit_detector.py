"""Point-in-time risk assessment for consequential quantum-computing claims.

The detector is deliberately not a truth meter.  It scores how much support the
available article provides for the breadth of its public claim.  Scores are
snapshots and always carry an assessment timestamp.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from html import escape
from typing import Callable, Dict, List, Optional

from summary_generator import SummaryGenerator


METHODOLOGY_VERSION = "qbd-1.0"
MAX_SOURCE_CHARS = int(os.getenv("QUANTUM_BS_DETECTOR_MAX_SOURCE_CHARS", "18000"))

DIMENSIONS = (
    ("physical_vs_logical_qubits", "Physical vs. logical qubits"),
    ("error_rates_and_fidelity", "Error rates and fidelity"),
    ("useful_circuit_depth", "Useful circuit depth"),
    ("classical_baseline", "Classical baseline"),
    ("postselection_and_filtering", "Postselection and filtering"),
    ("benchmark_relevance", "Benchmark relevance"),
    ("end_to_end_runtime", "End-to-end runtime"),
    ("scaling_assumptions", "Scaling assumptions"),
    ("reproducibility", "Reproducibility"),
    ("error_correction_overhead", "Error-correction overhead"),
    ("economic_relevance", "Economic relevance"),
    ("clear_claim_wording", "Clear claim wording"),
)

_TECHNICAL_RESULT_HINTS = (
    "advantage",
    "algorithm",
    "benchmark",
    "below threshold",
    "breakthrough",
    "circuit",
    "coherence",
    "demonstrat",
    "error correct",
    "fault toler",
    "fidelity",
    "logical qubit",
    "new architecture",
    "new code",
    "new processor",
    "new qubit",
    "quantum supremacy",
    "record",
    "researchers",
    "scientists",
    "speedup",
    "study",
)

_NON_TECHNICAL_EVENT_HINTS = (
    "acquisition",
    "appoints",
    "award funding",
    "contract award",
    "funding",
    "funding round",
    "government grant",
    "grant for",
    "grant awarded",
    "investment",
    "merger",
    "raises $",
    "raises €",
    "series a",
    "series b",
    "series c",
    "series d",
)


DETECTOR_PROMPT = """You are the Quantum Bullshit Detector, a skeptical but fair technical reviewer.

First decide whether the article's MAIN NEWS is eligible. Eligible stories make a consequential new public claim about quantum-computing hardware, error correction, algorithms, quantum advantage, scientific results, a technical roadmap, or commercial usefulness. The claim should plausibly move the industry if true. Funding rounds, grants, government programs, appointments, partnerships, acquisitions, events, and generic market commentary are not eligible unless the main news also contains a distinct, consequential technical result. Do not score background technical language attached to an otherwise ineligible business or policy event.

If eligible, identify one narrow, falsifiable primary claim and score all twelve dimensions from the evidence available in ARTICLE_TEXT:
0 = strong, specific evidence; fair measurement/benchmarking; enough detail to evaluate.
1 = real evidence, but meaningful caveats, ambiguity, missing comparisons, or uncertain scaling remain.
2 = high BS-risk due to omission, extrapolation, weak benchmarking, vague language, or evidence too narrow for the conclusion.

The dimensions are:
1. physical_vs_logical_qubits — Does a physical-qubit count substitute for specified logical performance?
2. error_rates_and_fidelity — Are relevant error rates, fidelity, leakage, measurement quality, conditions, and distributions disclosed?
3. useful_circuit_depth — Is demonstrated circuit depth sufficient for the claimed application?
4. classical_baseline — Is the comparison against a current, optimized classical method with fair assumptions and hardware budgets?
5. postselection_and_filtering — Are discarded data, survival rates, mitigation, and filtering disclosed and practical end to end?
6. benchmark_relevance — Does the benchmark directly support the claimed capability rather than being inflated into broad utility?
7. end_to_end_runtime — For speed/economic claims, are initialization, shots, decoding, classical loops, transfer, and post-processing counted?
8. scaling_assumptions — Is scaling demonstrated rather than merely roadmapped, including fabrication, controls, cooling, networking, and calibration bottlenecks?
9. reproducibility — Is there peer review, methodological detail, data/code, independent scrutiny, or reproduction appropriate to the maturity of the result?
10. error_correction_overhead — Are physical resources, gates, ancillas, decoding, latency, code choice, and magic-state costs exposed where relevant?
11. economic_relevance — For commercial claims, are full hardware/operating/integration costs and the best classical alternative addressed?
12. clear_claim_wording — Is the task, system, comparison, metric, and boundary condition narrow and falsifiable?

Do not invent external facts. Judge only the supplied material. Missing evidence should increase risk when that evidence is needed to support the claim. If a dimension is genuinely not implicated by the claim, score 0 and explicitly say why rather than manufacturing a penalty. A high score means claim-risk, not fraud or bad science.

Apply this evidence hierarchy when judging what the article supplies: peer-reviewed papers and technical appendices; reproducible preprints with released data/code; detailed technical reports; independently scrutinized company publications; and finally marketing or press material. A press release may describe evidence but must not outrank its underlying technical support. Treat the score as a point-in-time snapshot that may change with replication, better classical algorithms, or newly demonstrated milestones.

Return only valid JSON with this shape:
{
  "eligible": true,
  "eligibility_reason": "one sentence",
  "claim_type": "technical_breakthrough|company_claim|scientific_development|technical_roadmap|commercial_usefulness|not_eligible",
  "primary_claim": "one narrow claim, or empty when ineligible",
  "verdict": "one concise sentence separating demonstrated evidence from what remains unsupported",
  "dimensions": [
    {"id": "physical_vs_logical_qubits", "score": 0, "reason": "specific evidence-based reason"}
  ]
}

For eligible claims, return every dimension exactly once. For ineligible stories, return an empty dimensions array."""


def is_detector_candidate(
    title: str,
    summary: str = "",
    source_text: str = "",
    *,
    is_paper: bool = False,
) -> bool:
    """Cheaply reject obvious non-technical news before invoking the model."""
    headline_context = " ".join((title or "", summary or "")).lower()
    has_result = any(hint in headline_context for hint in _TECHNICAL_RESULT_HINTS)
    business_only = any(hint in headline_context for hint in _NON_TECHNICAL_EVENT_HINTS)

    if business_only and not has_result:
        return False
    if is_paper:
        return True
    if has_result:
        return True

    # This is already a quantum-news pipeline. Keep the deterministic gate
    # deliberately permissive and let the semantic review reject policy,
    # personnel, event, and generic market stories. That avoids missing a real
    # development merely because its headline uses an unfamiliar technical term.
    return True


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def _normalize_assessment(payload: Dict[str, object], assessed_at: datetime) -> Optional[Dict[str, object]]:
    if not isinstance(payload, dict) or not _coerce_bool(payload.get("eligible")):
        return None

    supplied = payload.get("dimensions")
    if not isinstance(supplied, list):
        return None
    by_id = {
        str(item.get("id", "")): item
        for item in supplied
        if isinstance(item, dict)
    }
    if any(dimension_id not in by_id for dimension_id, _ in DIMENSIONS):
        return None

    dimensions: List[Dict[str, object]] = []
    for dimension_id, label in DIMENSIONS:
        item = by_id[dimension_id]
        try:
            score = int(item.get("score"))
        except (TypeError, ValueError):
            return None
        if score not in (0, 1, 2):
            return None
        dimensions.append(
            {
                "id": dimension_id,
                "label": label,
                "score": score,
                "reason": re.sub(r"\s+", " ", str(item.get("reason", "") or "")).strip(),
            }
        )

    raw_score = sum(int(item["score"]) for item in dimensions)
    return {
        "eligible": True,
        "eligibility_reason": re.sub(
            r"\s+", " ", str(payload.get("eligibility_reason", "") or "")
        ).strip(),
        "claim_type": str(payload.get("claim_type", "company_claim") or "company_claim"),
        "primary_claim": re.sub(r"\s+", " ", str(payload.get("primary_claim", "") or "")).strip(),
        "verdict": re.sub(r"\s+", " ", str(payload.get("verdict", "") or "")).strip(),
        "dimensions": dimensions,
        "raw_score": raw_score,
        "risk_score": round(raw_score / 24 * 100),
        "assessed_at": assessed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "methodology_version": METHODOLOGY_VERSION,
    }


def assess_quantum_claim(
    title: str,
    summary: str,
    source_text: str,
    *,
    is_paper: bool = False,
    assessed_at: Optional[datetime] = None,
    analyzer: Optional[Callable[[str, str], Dict[str, object]]] = None,
) -> Optional[Dict[str, object]]:
    """Return a validated twelve-dimension assessment for an eligible claim.

    ``None`` means the story is ineligible, the model declined to score it, or
    the response was incomplete.  Incomplete assessments are never displayed.
    """
    if not is_detector_candidate(title, summary, source_text, is_paper=is_paper):
        return None

    user_payload = "\n".join(
        (
            "HEADLINE:\n%s" % (title or ""),
            "NEWSLETTER_SUMMARY:\n%s" % (summary or ""),
            "ARTICLE_TEXT:\n%s" % (source_text or "")[:MAX_SOURCE_CHARS],
        )
    )
    try:
        if analyzer is None:
            generator = SummaryGenerator(source_text or summary or title)
            payload = generator.generate_json_summary(DETECTOR_PROMPT, user_payload)
        else:
            payload = analyzer(DETECTOR_PROMPT, user_payload)
    except Exception as exc:
        print("Warning: Quantum Bullshit Detector assessment failed: %s" % exc)
        return None

    timestamp = assessed_at or datetime.now(timezone.utc)
    return _normalize_assessment(payload, timestamp)


def render_detector_badge(assessment: Optional[Dict[str, object]]) -> str:
    """Render the score and its snapshot time beside a story headline."""
    if not assessment or not assessment.get("eligible"):
        return ""
    try:
        score = int(assessment["risk_score"])
    except (KeyError, TypeError, ValueError):
        return ""
    score = max(0, min(100, score))
    if score <= 33:
        background, foreground = "#d1fae5", "#065f46"
    elif score <= 66:
        background, foreground = "#fef3c7", "#92400e"
    else:
        background, foreground = "#fee2e2", "#991b1b"

    assessed_at = str(assessment.get("assessed_at", "") or "")
    visible_time = assessed_at.replace("T", " ").replace("Z", " UTC")
    if "." in visible_time:
        visible_time = visible_time.split(".", 1)[0] + " UTC"
    profile = "; ".join(
        "%s: %s/2" % (item.get("label", item.get("id", "Dimension")), item.get("score", "?"))
        for item in assessment.get("dimensions", [])
        if isinstance(item, dict)
    )
    verdict = str(assessment.get("verdict", "") or "")
    tooltip = ". ".join(
        part
        for part in ("Point-in-time claim-risk, not a truth meter", verdict, profile)
        if part
    )
    return (
        '<span class="qbd-badge" title="%s" style="display:inline-flex; align-items:center; '
        'gap:5px; margin-left:8px; padding:4px 7px; border-radius:999px; '
        'background:%s; color:%s; font:600 0.68rem/1.2 Arial,sans-serif; '
        'vertical-align:middle; white-space:nowrap;">'
        'Quantum BS-risk %d/100 <span aria-hidden="true">·</span> '
        '<time datetime="%s">assessed %s</time></span>'
    ) % (
        escape(tooltip, quote=True),
        background,
        foreground,
        score,
        escape(assessed_at, quote=True),
        escape(visible_time),
    )
