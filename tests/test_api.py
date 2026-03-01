"""
test_api.py — Integration tests for the FastAPI endpoints.

These tests use FastAPI's TestClient, which sends real HTTP requests to the
app in-process (no network needed). The CrewAI crew and MLflow are mocked
so these tests run instantly without API keys.

How the mocking works:
- 'run_for_invoice' is the function that calls the LLM agents. We replace
  it with a function that returns a hardcoded result instantly.
- 'run_scorers' and 'log_scores_to_mlflow' are similarly replaced.
- MLflow is mocked globally via conftest.py.

The TestClient is created once per module (scope="module") for speed.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app import app


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

VALID_INVOICE = {
    "invoice_number": "INV-001",
    "company_name": "Blackstone Retail Ltd",
    "amount": 47500.00,
    "due_date": "2025-07-01",
}

MOCK_DRAFT_RESULT = {
    "invoice_number": "INV-001",
    "tone_score": 0,
    "subject": "Final Notice: Invoice INV-001 — Blackstone Retail Ltd",
    "description": (
        "Dear Blackstone Retail Ltd Finance Team, "
        "This is a final notice for invoice INV-001 for $47,500.00. "
        "Please remit payment within 48 hours. Regards, Accounts Receivable"
    ),
}

MOCK_SCORES = [
    {"name": "tone_consistency", "value": True, "rationale": "Firm markers detected"},
    {"name": "completeness_overall", "value": True, "rationale": "All elements present"},
    {"name": "guardrail_pass", "value": True, "rationale": "No issues"},
]


# ---------------------------------------------------------------------------
# Client fixture — shared across all tests in this module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Create a TestClient for the FastAPI app, shared across all tests."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_returns_ok_status(self, client):
        data = response = client.get("/health").json()
        assert data["status"] == "ok"

    def test_returns_service_name(self, client):
        data = client.get("/health").json()
        assert data["service"] == "cash-collection-drafter"

    def test_returns_version(self, client):
        data = client.get("/health").json()
        assert "version" in data


# ---------------------------------------------------------------------------
# GET /.well-known/agent.json
# ---------------------------------------------------------------------------

class TestAgentCard:

    def test_returns_200(self, client):
        response = client.get("/.well-known/agent.json")
        assert response.status_code == 200

    def test_has_required_a2a_fields(self, client):
        data = client.get("/.well-known/agent.json").json()
        assert "name" in data
        assert "description" in data
        assert "skills" in data

    def test_skills_is_a_list(self, client):
        data = client.get("/.well-known/agent.json").json()
        assert isinstance(data["skills"], list)
        assert len(data["skills"]) > 0


# ---------------------------------------------------------------------------
# POST /draft
# ---------------------------------------------------------------------------

class TestDraftEndpoint:

    def _post_draft(self, client, invoices):
        with (
            patch("app.run_for_invoice", return_value=MOCK_DRAFT_RESULT),
            patch("app.run_scorers", return_value=MOCK_SCORES),
            patch("app.log_scores_to_mlflow"),
        ):
            return client.post("/draft", json={"invoices": invoices})

    def test_single_invoice_returns_200(self, client):
        response = self._post_draft(client, [VALID_INVOICE])
        assert response.status_code == 200

    def test_result_has_correct_structure(self, client):
        data = self._post_draft(client, [VALID_INVOICE]).json()
        assert "results" in data
        assert "errors" in data

    def test_result_contains_one_item(self, client):
        data = self._post_draft(client, [VALID_INVOICE]).json()
        assert len(data["results"]) == 1
        assert len(data["errors"]) == 0

    def test_result_fields_are_present(self, client):
        item = self._post_draft(client, [VALID_INVOICE]).json()["results"][0]
        assert "invoice_number" in item
        assert "tone_score" in item
        assert "subject" in item
        assert "description" in item

    def test_result_invoice_number_matches_input(self, client):
        item = self._post_draft(client, [VALID_INVOICE]).json()["results"][0]
        assert item["invoice_number"] == "INV-001"

    def test_invalid_amount_returns_422(self, client):
        """amount must be > 0 — FastAPI validates this automatically."""
        bad_invoice = {**VALID_INVOICE, "amount": -100}
        response = client.post("/draft", json={"invoices": [bad_invoice]})
        assert response.status_code == 422

    def test_zero_amount_returns_422(self, client):
        bad_invoice = {**VALID_INVOICE, "amount": 0}
        response = client.post("/draft", json={"invoices": [bad_invoice]})
        assert response.status_code == 422

    def test_empty_invoices_list_returns_422(self, client):
        """invoices list must have at least 1 item."""
        response = client.post("/draft", json={"invoices": []})
        assert response.status_code == 422

    def test_missing_invoice_number_returns_422(self, client):
        bad_invoice = {k: v for k, v in VALID_INVOICE.items() if k != "invoice_number"}
        response = client.post("/draft", json={"invoices": [bad_invoice]})
        assert response.status_code == 422

    def test_crew_error_goes_into_errors_list(self, client):
        """If the crew raises an exception, it ends up in 'errors', not a 500."""
        with (
            patch("app.run_for_invoice", side_effect=RuntimeError("LLM timeout")),
            patch("app.log_scores_to_mlflow"),
        ):
            response = client.post("/draft", json={"invoices": [VALID_INVOICE]})

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["invoice_number"] == "INV-001"
        assert "LLM timeout" in data["errors"][0]["error"]


# ---------------------------------------------------------------------------
# POST /a2a
# ---------------------------------------------------------------------------

class TestA2AEndpoint:

    def test_invalid_json_returns_400(self, client):
        response = client.post(
            "/a2a",
            content="this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_parse_error_response_shape(self, client):
        response = client.post(
            "/a2a",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert "error" in data
        assert data["error"]["code"] == -32700

    def test_valid_tasks_send(self, client):
        """A well-formed tasks/send request should return a JSON-RPC response."""
        with (
            patch("a2a.task_handler.run_for_invoice", return_value=MOCK_DRAFT_RESULT),
        ):
            payload = {
                "jsonrpc": "2.0",
                "id": "test-1",
                "method": "tasks/send",
                "params": {
                    "message": {
                        "parts": [{
                            "type": "data",
                            "data": {"invoices": [VALID_INVOICE]},
                        }]
                    }
                },
            }
            response = client.post("/a2a", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert "result" in data or "error" in data
