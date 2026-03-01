"""
test_scorers.py — Unit tests for the evaluation scorers.

The scorers analyse a drafted email and return pass/fail metrics.
These tests cover all three scorers with good and bad email examples.

No LLM calls here — the scorers are pure rule-based Python functions.
"""

import pytest
from evaluation.scorers import (
    tone_consistency_scorer,
    completeness_scorer,
    guardrail_scorer,
    run_scorers,
)


# ---------------------------------------------------------------------------
# Sample emails — reused across tests
# ---------------------------------------------------------------------------

# A firm email that should pass tone score 0
FIRM_EMAIL = {
    "tone_score": 0,
    "description": (
        "Dear Finance Team, This is a final notice regarding your outstanding balance. "
        "You must remit payment within 48 hours or face legal proceedings and service suspension. "
        "Failure to pay will result in serious consequences. Regards, Accounts Receivable"
    ),
}

# A polite email that should pass tone score 5
POLITE_EMAIL = {
    "tone_score": 5,
    "description": (
        "Dear valued partner, Thank you for your continued partnership with us. "
        "We appreciate your business and would be grateful if you could settle the outstanding "
        "balance at your earliest convenience. Please do not hesitate to reach out. "
        "Warm regards, Accounts Receivable"
    ),
}

# A neutral email (tone 3) — no strict marker check
NEUTRAL_EMAIL = {
    "tone_score": 3,
    "description": (
        "Dear Client, We are writing to remind you that invoice INV-004 for $5,500.00 "
        "is due for payment. Please remit payment by the due date. Regards, AR Team"
    ),
}

# A complete well-structured email (used for completeness tests)
COMPLETE_EMAIL = {
    "tone_score": 2,
    "description": (
        "Dear John Smith, We are writing regarding invoice INV-003 for $9,200.00 "
        "which is now overdue. Please remit payment by 2025-08-01 to avoid further action. "
        "Regards, Accounts Receivable Team"
    ),
}

# An email missing most structural elements
BARE_EMAIL = {
    "tone_score": 2,
    "description": "The balance is overdue.",
}


# ---------------------------------------------------------------------------
# Tone Consistency Scorer
# ---------------------------------------------------------------------------

class TestToneConsistency:

    def test_firm_email_with_firm_score_passes(self):
        result = tone_consistency_scorer(FIRM_EMAIL)
        assert result["name"] == "tone_consistency"
        assert result["value"] is True

    def test_polite_email_with_polite_score_passes(self):
        result = tone_consistency_scorer(POLITE_EMAIL)
        assert result["value"] is True

    def test_neutral_email_auto_passes(self):
        """Scores 2-3 are in the neutral range and always pass."""
        result = tone_consistency_scorer(NEUTRAL_EMAIL)
        assert result["value"] is True

    def test_soft_email_with_firm_score_fails(self):
        """A gentle email with tone_score 0 should fail — tone mismatch."""
        soft_but_firm_score = {
            "tone_score": 0,
            "description": "Dear Client, Hope you are well. Please pay when you can. Best wishes.",
        }
        result = tone_consistency_scorer(soft_but_firm_score)
        assert result["value"] is False

    def test_harsh_email_with_polite_score_fails(self):
        """A demanding email with tone_score 5 should fail — tone mismatch."""
        harsh_but_polite_score = {
            "tone_score": 5,
            "description": "You must pay immediately or face consequences. Final demand.",
        }
        result = tone_consistency_scorer(harsh_but_polite_score)
        assert result["value"] is False

    def test_missing_tone_score_fails(self):
        result = tone_consistency_scorer({"tone_score": -1, "description": "some text"})
        assert result["value"] is False

    def test_result_has_rationale(self):
        result = tone_consistency_scorer(FIRM_EMAIL)
        assert "rationale" in result
        assert isinstance(result["rationale"], str)


# ---------------------------------------------------------------------------
# Completeness Scorer
# ---------------------------------------------------------------------------

class TestCompleteness:

    def test_complete_email_passes_all_checks(self):
        results = completeness_scorer(COMPLETE_EMAIL)
        overall = next(r for r in results if r["name"] == "completeness_overall")
        assert overall["value"] is True

    def test_returns_six_results(self):
        """5 individual element checks + 1 overall = 6 results."""
        results = completeness_scorer(COMPLETE_EMAIL)
        assert len(results) == 6

    def test_result_names_match_expected(self):
        results = completeness_scorer(COMPLETE_EMAIL)
        names = {r["name"] for r in results}
        assert "completeness_greeting" in names
        assert "completeness_invoice_reference" in names
        assert "completeness_amount" in names
        assert "completeness_call_to_action" in names
        assert "completeness_sign_off" in names
        assert "completeness_overall" in names

    def test_bare_email_fails_overall(self):
        results = completeness_scorer(BARE_EMAIL)
        overall = next(r for r in results if r["name"] == "completeness_overall")
        assert overall["value"] is False

    def test_greeting_detected(self):
        results = completeness_scorer(COMPLETE_EMAIL)
        greeting = next(r for r in results if r["name"] == "completeness_greeting")
        assert greeting["value"] is True

    def test_missing_greeting_fails(self):
        no_greeting = {
            "description": (
                "Invoice INV-003 for $9,200.00 is overdue. "
                "Please remit payment. Regards, AR Team"
            )
        }
        results = completeness_scorer(no_greeting)
        greeting = next(r for r in results if r["name"] == "completeness_greeting")
        assert greeting["value"] is False

    def test_amount_detected(self):
        results = completeness_scorer(COMPLETE_EMAIL)
        amount = next(r for r in results if r["name"] == "completeness_amount")
        assert amount["value"] is True


# ---------------------------------------------------------------------------
# Guardrail Scorer
# ---------------------------------------------------------------------------

class TestGuardrail:

    def test_clean_professional_email_passes(self):
        result = guardrail_scorer({"description": "Dear Client, please pay your invoice. Regards."})
        assert result["name"] == "guardrail_pass"
        assert result["value"] is True

    def test_insult_flagged(self):
        result = guardrail_scorer({"description": "You are an idiot. Pay now."})
        assert result["value"] is False

    def test_scammer_accusation_flagged(self):
        result = guardrail_scorer({"description": "You are a scammer and we hate working with you."})
        assert result["value"] is False

    def test_excessive_aggression_flagged(self):
        result = guardrail_scorer({"description": "We will destroy your business if you don't pay."})
        assert result["value"] is False

    def test_legal_action_mention_is_allowed(self):
        """Mentioning legal action is firm but not offensive — should pass."""
        result = guardrail_scorer({
            "description": "Failure to pay may result in legal action. Regards, AR Team."
        })
        assert result["value"] is True

    def test_result_has_rationale(self):
        result = guardrail_scorer({"description": "Please pay. Thank you."})
        assert "rationale" in result


# ---------------------------------------------------------------------------
# run_scorers — integration
# ---------------------------------------------------------------------------

class TestRunScorers:

    def test_returns_all_expected_metric_names(self):
        output = {
            "invoice_number": "INV-001",
            "tone_score": 0,
            "subject": "Final Notice: INV-001",
            "description": (
                "Dear Finance Team, This is a final notice for invoice INV-001 for $47,500.00. "
                "Please pay within 48 hours or face legal proceedings. "
                "Regards, Accounts Receivable"
            ),
        }
        results = run_scorers(output)
        names = [r["name"] for r in results]

        assert "tone_consistency" in names
        assert "completeness_overall" in names
        assert "guardrail_pass" in names
        # LLM judge disabled by default (LLM_JUDGE_ENABLED not set to true)
        assert "llm_judge_professional_tone" not in names

    def test_each_result_has_name_value_rationale(self):
        results = run_scorers(FIRM_EMAIL | {"invoice_number": "INV-001", "subject": "Test"})
        for result in results:
            assert "name" in result
            assert "value" in result
            assert "rationale" in result

    def test_value_is_bool_or_none(self):
        results = run_scorers(FIRM_EMAIL | {"invoice_number": "INV-001", "subject": "Test"})
        for result in results:
            assert isinstance(result["value"], (bool, type(None)))
