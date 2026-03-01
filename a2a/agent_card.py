"""
agent_card.py — Builds a spec-compliant Google A2A Agent Card.

The Agent Card is served at GET /.well-known/agent.json
per the A2A protocol specification.

Reference: https://a2a-protocol.org/latest/specification/
"""

from typing import Any


def build_agent_card(base_url: str) -> dict[str, Any]:
    """
    Build an A2A Agent Card dict.

    Args:
        base_url: Public-facing root URL, e.g. 'http://localhost:8000'.
                  Injected from the BASE_URL environment variable at runtime.

    Returns:
        A dict matching the A2A AgentCard JSON schema.
    """
    return {
        "name": "Cash Collection Email Drafter",
        "description": (
            "An AI agent that processes batches of invoice data and drafts professional "
            "collection emails. For each invoice, the agent fetches CRM client data, "
            "analyzes the appropriate communication tone on a 0-5 scale, and generates "
            "a tailored collection email with a subject and body."
        ),
        "version": "1.0.0",
        "url": f"{base_url}/a2a",
        "provider": {
            "name": "Accounts Receivable Automation",
            "url": base_url,
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "draft_collection_emails",
                "name": "Draft Collection Emails",
                "description": (
                    "Accepts a batch of invoices (each with invoice_number, company_name, "
                    "amount, due_date). For each invoice, fetches CRM data via MCP tool, "
                    "decides communication tone (0=firm/strict → 5=professional/polite) "
                    "using a structured rubric, and returns a drafted collection email "
                    "with subject and description per invoice."
                ),
                "tags": [
                    "email",
                    "collections",
                    "invoices",
                    "accounts-receivable",
                    "drafting",
                    "crm",
                ],
                "examples": [
                    "Draft a collection email for invoice INV-001",
                    "Process a batch of overdue invoices and draft outreach emails",
                    "Generate collection emails tailored to client payment history",
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "invoices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["invoice_number", "company_name", "amount", "due_date"],
                                "properties": {
                                    "invoice_number": {"type": "string", "example": "INV-001"},
                                    "company_name": {"type": "string", "example": "Acme Corp"},
                                    "amount": {"type": "number", "example": 12500.00},
                                    "due_date": {"type": "string", "format": "date", "example": "2025-09-15"},
                                },
                            },
                        }
                    },
                    "required": ["invoices"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "invoice_number": {"type": "string"},
                                    "tone_score": {"type": "integer", "minimum": 0, "maximum": 5},
                                    "subject": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                            },
                        },
                        "errors": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "invoice_number": {"type": "string"},
                                    "error": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        ],
        "authentication": None,
    }
