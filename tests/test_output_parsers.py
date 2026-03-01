"""
test_output_parsers.py — Unit tests for the LLM output parsers in email_crew.py.

LLMs sometimes return JSON wrapped in markdown code fences, or add preamble
text before the JSON. These parsers extract the data robustly.

We test three scenarios for each parser:
  1. Clean JSON (ideal case)
  2. JSON inside ```json ... ``` code fences (common LLM habit)
  3. Malformed/unrecognisable output (fallback behaviour)
"""

import pytest
from crew.email_crew import (
    _extract_tone_score,
    _extract_email_parts,
    _strip_code_fences,
)


# ---------------------------------------------------------------------------
# Helper — simulate a CrewAI TaskOutput object
# ---------------------------------------------------------------------------

class FakeTaskOutput:
    """Mimics the CrewAI TaskOutput object which has a .raw attribute."""
    def __init__(self, raw: str):
        self.raw = raw


# ---------------------------------------------------------------------------
# _strip_code_fences
# ---------------------------------------------------------------------------

class TestStripCodeFences:

    def test_strips_json_fences(self):
        raw = '```json\n{"key": "value"}\n```'
        assert _strip_code_fences(raw) == '{"key": "value"}'

    def test_strips_plain_fences(self):
        raw = '```\nhello world\n```'
        assert _strip_code_fences(raw) == 'hello world'

    def test_no_fences_unchanged(self):
        raw = '{"key": "value"}'
        assert _strip_code_fences(raw) == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = '  {"key": "value"}  '
        assert _strip_code_fences(raw) == '{"key": "value"}'


# ---------------------------------------------------------------------------
# _extract_tone_score
# ---------------------------------------------------------------------------

class TestExtractToneScore:

    def test_plain_json(self):
        output = FakeTaskOutput('{"tone_score": 3, "reasoning": "Standard client"}')
        assert _extract_tone_score(output) == 3

    def test_json_in_code_fences(self):
        output = FakeTaskOutput('```json\n{"tone_score": 1, "reasoning": "Late payer"}\n```')
        assert _extract_tone_score(output) == 1

    def test_all_valid_scores(self):
        """All scores 0-5 should parse correctly."""
        for score in range(6):
            output = FakeTaskOutput(f'{{"tone_score": {score}, "reasoning": "test"}}')
            assert _extract_tone_score(output) == score

    def test_regex_fallback_when_json_malformed(self):
        """If JSON is broken, falls back to regex search for tone_score.
        The regex matches the pattern "tone_score": <digit> (with double quotes).
        """
        output = FakeTaskOutput('Based on the data: "tone_score": 2, this seems right.')
        assert _extract_tone_score(output) == 2

    def test_regex_fallback_with_quotes(self):
        output = FakeTaskOutput('I think "tone_score": 4 is right here.')
        assert _extract_tone_score(output) == 4

    def test_unrecognisable_output_returns_minus_one(self):
        """If nothing can be parsed, returns -1 as a sentinel value."""
        output = FakeTaskOutput("I cannot determine the appropriate tone for this client.")
        assert _extract_tone_score(output) == -1

    def test_accepts_plain_string(self):
        """Also works with a plain string, not just a TaskOutput object."""
        assert _extract_tone_score('{"tone_score": 5, "reasoning": "VIP"}') == 5

    def test_extra_text_before_json(self):
        """LLMs sometimes add preamble before the JSON."""
        output = FakeTaskOutput(
            'Based on the client data, here is my assessment:\n'
            '{"tone_score": 0, "reasoning": "Multiple defaults"}'
        )
        assert _extract_tone_score(output) == 0


# ---------------------------------------------------------------------------
# _extract_email_parts
# ---------------------------------------------------------------------------

class TestExtractEmailParts:

    def test_plain_json(self):
        output = FakeTaskOutput('{"subject": "Invoice Due", "description": "Dear Client, pay now."}')
        subject, description = _extract_email_parts(output)
        assert subject == "Invoice Due"
        assert description == "Dear Client, pay now."

    def test_json_in_code_fences(self):
        output = FakeTaskOutput(
            '```json\n{"subject": "Final Notice", "description": "Dear Team, pay now."}\n```'
        )
        subject, description = _extract_email_parts(output)
        assert subject == "Final Notice"
        assert description == "Dear Team, pay now."

    def test_empty_fields_return_empty_strings(self):
        output = FakeTaskOutput('{"subject": "", "description": ""}')
        subject, description = _extract_email_parts(output)
        assert subject == ""
        assert description == ""

    def test_missing_subject_returns_empty(self):
        output = FakeTaskOutput('{"description": "Dear Client, pay now."}')
        subject, description = _extract_email_parts(output)
        assert subject == ""
        assert description == "Dear Client, pay now."

    def test_multiline_description(self):
        """Email bodies span multiple lines."""
        body = "Dear John,\n\nPlease pay invoice INV-001.\n\nRegards,\nAR Team"
        output = FakeTaskOutput(f'{{"subject": "Invoice", "description": "{body}"}}')
        subject, description = _extract_email_parts(output)
        assert subject == "Invoice"
        # Description may be slightly reformatted, just check key content
        assert "John" in description or "John" in description

    def test_accepts_plain_string(self):
        raw = '{"subject": "Reminder", "description": "Please pay."}'
        subject, description = _extract_email_parts(raw)
        assert subject == "Reminder"
        assert description == "Please pay."
