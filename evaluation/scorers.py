"""
scorers.py — MLflow custom scorers for collection email evaluation.

Three rule-based scorers + one optional LLM-as-judge:

1. tone_consistency_scorer  — checks email language matches the tone score
2. completeness_scorer       — checks all 5 structural elements are present
3. guardrail_scorer          — flags inappropriate/offensive content
4. LLM-as-judge (Guidelines) — optional, enabled via LLM_JUDGE_ENABLED=true

Usage:
    from evaluation.scorers import get_active_scorers, run_scorers
    results = run_scorers(output_dict)
    # returns list of {"name": str, "value": bool|float, "rationale": str}
"""

import os
import re
import logging
from typing import Any

import mlflow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scorer 1: Tone Consistency
# ---------------------------------------------------------------------------
_FIRM_MARKERS = [
    r"\bimmediate\b", r"legal\s+(?:action|proceedings)", r"\b48\s*hours?\b",
    r"\bfinal\s+notice\b", r"\bdemand\b", r"\bconsequences\b",
    r"service\s+suspension", r"overdue\s+notice",
]

_POLITE_MARKERS = [
    r"\bappreciate\b", r"\bvalued?\s+partner\b", r"\bthank\s+you\b",
    r"\bplease\s+do\s+not\s+hesitate\b", r"at\s+your\s+earliest\s+convenience",
    r"\bcontinued\s+partnership\b", r"\bgrateful\b", r"\bwarm\b",
]


def tone_consistency_scorer(output: dict) -> dict:
    """
    Checks whether the email language is consistent with the decided tone score.

    - Scores 0-1: expects at least one firm marker keyword
    - Scores 4-5: expects at least one polite marker keyword
    - Scores 2-3: neutral range, accepted without strict marker check
    """
    email_text: str = (output.get("description") or "").lower()
    tone_score: int = output.get("tone_score", -1)

    if tone_score < 0:
        return {
            "name": "tone_consistency",
            "value": False,
            "rationale": f"tone_score missing or invalid ({tone_score})",
        }

    if tone_score <= 1:
        hit = any(re.search(p, email_text) for p in _FIRM_MARKERS)
        rationale = (
            f"Tone {tone_score} (firm): firm language markers "
            f"{'✓ detected' if hit else '✗ NOT found — email may be too soft'}."
        )
    elif tone_score >= 4:
        hit = any(re.search(p, email_text) for p in _POLITE_MARKERS)
        rationale = (
            f"Tone {tone_score} (polite): polite language markers "
            f"{'✓ detected' if hit else '✗ NOT found — email may be too harsh'}."
        )
    else:
        hit = True
        rationale = f"Tone {tone_score} in neutral range (2-3) — accepted without strict marker check."

    return {"name": "tone_consistency", "value": hit, "rationale": rationale}


# ---------------------------------------------------------------------------
# Scorer 2: Completeness
# ---------------------------------------------------------------------------
_REQUIRED_ELEMENTS: dict[str, list[str]] = {
    "greeting": [r"\bdear\b", r"\bhello\b", r"\bhi\b"],
    "invoice_reference": [r"inv[-\s]?\d+", r"invoice\s+(?:number\s+)?#?\s*\w+"],
    "amount": [r"\$[\d,]+", r"\busd\b", r"outstanding\s+(?:balance|amount)", r"balance\s+of"],
    "call_to_action": [
        r"please\s+(?:pay|remit|arrange|transfer|make)",
        r"payment\s+(?:by|before|due|deadline)",
        r"\bsettle\b", r"\bremit\b",
    ],
    "sign_off": [r"\bregards\b", r"\bsincerely\b", r"\bthank\s+you\b", r"\byours\s+(?:truly|faithfully|sincerely)\b"],
}


def completeness_scorer(output: dict) -> list[dict]:
    """
    Checks that the email contains all required structural elements.
    Returns one result per element plus an overall completeness result.
    """
    email_text: str = (output.get("description") or "").lower()
    results = []
    all_present = True

    for element, patterns in _REQUIRED_ELEMENTS.items():
        found = any(re.search(p, email_text) for p in patterns)
        if not found:
            all_present = False
        results.append({
            "name": f"completeness_{element}",
            "value": found,
            "rationale": f"'{element}' {'✓ detected' if found else '✗ MISSING'} in email body.",
        })

    results.append({
        "name": "completeness_overall",
        "value": all_present,
        "rationale": (
            "All required structural elements present." if all_present
            else "One or more required elements missing — see individual completeness_* metrics."
        ),
    })
    return results


# ---------------------------------------------------------------------------
# Scorer 3: Guardrail
# ---------------------------------------------------------------------------
_OFFENSIVE_PATTERNS = [
    r"\bstupid\b", r"\bidiot\b", r"\bmoron\b", r"\bfool\b",
    r"\b(?:you are|you're)\s+(?:a\s+)?(?:liar|cheat|fraud)\b",
    r"\bscam(?:mer)?\b",
    r"\bthreaten\b",
    r"\bhate\b",
    r"\bworthless\b",
]

_EXCESSIVE_AGGRESSION_PATTERNS = [
    r"we\s+will\s+destroy",
    r"(?:ruin|destroy)\s+your\s+(?:business|company|reputation)",
    r"(?:take|drag)\s+you\s+to\s+court\s+immediately",
]


def guardrail_scorer(output: dict) -> dict:
    """
    Detects inappropriate, offensive, or excessively aggressive content.
    Returns True (passed) if none found.
    """
    email_text: str = (output.get("description") or "").lower()

    offensive_hits = [p for p in _OFFENSIVE_PATTERNS if re.search(p, email_text)]
    aggression_hits = [p for p in _EXCESSIVE_AGGRESSION_PATTERNS if re.search(p, email_text)]
    violations = offensive_hits + aggression_hits

    passed = len(violations) == 0
    return {
        "name": "guardrail_pass",
        "value": passed,
        "rationale": (
            "✓ No inappropriate content detected." if passed
            else f"✗ Flagged patterns: {violations}"
        ),
    }


# ---------------------------------------------------------------------------
# LLM-as-judge (optional)
# ---------------------------------------------------------------------------
def _llm_judge_scorer(output: dict) -> dict:
    """
    Uses the MLflow Guidelines scorer (LLM-as-judge) to evaluate tone appropriateness.
    Only called when LLM_JUDGE_ENABLED=true.
    """
    try:
        from mlflow.genai.scorers import Guidelines  # type: ignore[import]

        judge = Guidelines(
            name="professional_tone_judge",
            guidelines=(
                "The email should be professional, clear, and appropriate for a business "
                "collection context. It must not contain threats, offensive language, or "
                "content that could damage the business relationship beyond what is warranted "
                "by the client's payment history. The tone must match the stated tone_score: "
                "score 0 should be firm but not abusive; score 5 should be warm and appreciative."
            ),
        )
        # Run the judge against the email text
        result = judge.score(
            inputs={"tone_score": output.get("tone_score")},
            outputs=output.get("description", ""),
        )
        return {
            "name": "llm_judge_professional_tone",
            "value": result.value if hasattr(result, "value") else result,
            "rationale": result.rationale if hasattr(result, "rationale") else "LLM judge completed",
        }
    except Exception as exc:
        logger.warning("LLM judge failed: %s", exc)
        return {
            "name": "llm_judge_professional_tone",
            "value": None,
            "rationale": f"LLM judge skipped: {exc}",
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_scorers(output: dict) -> list[dict]:
    """
    Run all active scorers against a single invoice output dict.

    Args:
        output: dict with keys invoice_number, tone_score, subject, description

    Returns:
        Flat list of {"name": str, "value": bool|None, "rationale": str}
    """
    results: list[dict] = []

    # Scorer 1: Tone consistency
    results.append(tone_consistency_scorer(output))

    # Scorer 2: Completeness (returns list)
    results.extend(completeness_scorer(output))

    # Scorer 3: Guardrail
    results.append(guardrail_scorer(output))

    # Optional LLM judge
    if os.environ.get("LLM_JUDGE_ENABLED", "false").lower() == "true":
        results.append(_llm_judge_scorer(output))

    return results


def log_scores_to_mlflow(scores: list[dict]) -> None:
    """
    Log scorer results as MLflow metrics in the active run.
    bool values → 1.0/0.0; None values → skipped.
    """
    for score in scores:
        value = score.get("value")
        if value is None:
            continue
        metric_value = float(value) if isinstance(value, bool) else float(value)
        try:
            mlflow.log_metric(score["name"], metric_value)
            logger.debug("Logged MLflow metric %s = %s", score["name"], metric_value)
        except Exception as exc:
            logger.warning("Failed to log MLflow metric %s: %s", score["name"], exc)
