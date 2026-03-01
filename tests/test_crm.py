"""
test_crm.py — Unit tests for the CRM data store.

These tests verify that:
- Known invoice numbers return the correct client records
- Unknown invoice numbers return None (not an error)
- Every record has all the required fields
- All 8 test records exist and amounts are positive

No mocking needed here — crm.py has no external dependencies.
"""

import pytest
from crm import fetch_client, CRM_DATA


# ---------------------------------------------------------------------------
# Basic lookup
# ---------------------------------------------------------------------------

def test_fetch_known_invoice_returns_record():
    record = fetch_client("INV-001")
    assert record is not None


def test_fetch_returns_correct_client():
    record = fetch_client("INV-001")
    assert record["client_name"] == "Blackstone Retail Ltd"
    assert record["client_email"] == "finance@blackstone-retail.com"
    assert record["invoice_number"] == "INV-001"


def test_fetch_unknown_invoice_returns_none():
    """An invoice not in the CRM should return None, not raise an exception."""
    assert fetch_client("INV-999") is None
    assert fetch_client("") is None
    assert fetch_client("INVALID") is None


# ---------------------------------------------------------------------------
# All 8 records present and correctly shaped
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "invoice_number",
    "client_name",
    "client_email",
    "relationship_info",
    "outstanding_amount",
    "due_date",
}


def test_all_eight_records_exist():
    for i in range(1, 9):
        inv = f"INV-00{i}"
        assert fetch_client(inv) is not None, f"{inv} missing from CRM"


def test_all_records_have_required_fields():
    for inv_num, record in CRM_DATA.items():
        for field in REQUIRED_FIELDS:
            assert field in record, f"{inv_num} is missing field '{field}'"


def test_all_amounts_are_positive():
    for inv_num, record in CRM_DATA.items():
        assert record["outstanding_amount"] > 0, f"{inv_num} has non-positive amount"


def test_all_due_dates_are_strings():
    """Due dates should be ISO 8601 strings like '2025-07-01'."""
    for inv_num, record in CRM_DATA.items():
        assert isinstance(record["due_date"], str), f"{inv_num} due_date is not a string"
        assert len(record["due_date"]) == 10, f"{inv_num} due_date doesn't look like YYYY-MM-DD"


# ---------------------------------------------------------------------------
# Relationship info covers all tone tiers (ensures full test coverage)
# ---------------------------------------------------------------------------

def test_tone_zero_client_exists():
    """INV-001 should be a multiple-defaults client (maps to tone 0)."""
    record = fetch_client("INV-001")
    assert "multiple defaults" in record["relationship_info"]


def test_tone_five_client_exists():
    """INV-006 should be a high-value client (maps to tone 5)."""
    record = fetch_client("INV-006")
    assert "high value" in record["relationship_info"]
