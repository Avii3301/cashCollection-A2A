"""
crm.py — Mock CRM data store.

In production this would be replaced by a real CRM API call.
Records are keyed by invoice_number for O(1) lookup.

relationship_info values map to tone rubric tiers:
  "client with multiple defaults in the past"  → score 0 (firm/strict)
  "repeat late payer"                           → score 1 (assertive)
  "overdue, no prior defaults"                  → score 2 (direct)
  "standard client"                             → score 3 (neutral)
  "new client"                                  → score 4 (courteous)
  "high value client" / "long-term client"      → score 5 (polite)
"""

from typing import TypedDict, Optional


class ClientRecord(TypedDict):
    invoice_number: str
    client_name: str
    client_email: str
    relationship_info: str
    outstanding_amount: float
    due_date: str  # ISO 8601 e.g. "2025-09-15"


# ---------------------------------------------------------------------------
# Mock data — 8 records covering all tone tiers
# ---------------------------------------------------------------------------
CRM_DATA: dict[str, ClientRecord] = {
    # Tone 0 — firm/strict: multiple defaults
    "INV-001": {
        "invoice_number": "INV-001",
        "client_name": "Blackstone Retail Ltd",
        "client_email": "finance@blackstone-retail.com",
        "relationship_info": "client with multiple defaults in the past",
        "outstanding_amount": 47500.00,
        "due_date": "2025-07-01",
    },
    # Tone 1 — assertive: repeat late payer
    "INV-002": {
        "invoice_number": "INV-002",
        "client_name": "Meridian Logistics",
        "client_email": "ap@meridian-logistics.com",
        "relationship_info": "repeat late payer",
        "outstanding_amount": 12800.00,
        "due_date": "2025-07-15",
    },
    # Tone 2 — direct: overdue, no prior defaults
    "INV-003": {
        "invoice_number": "INV-003",
        "client_name": "Crestwood Manufacturing",
        "client_email": "billing@crestwood-mfg.com",
        "relationship_info": "overdue, no prior defaults",
        "outstanding_amount": 9200.00,
        "due_date": "2025-08-01",
    },
    # Tone 3 — neutral: standard client, first reminder
    "INV-004": {
        "invoice_number": "INV-004",
        "client_name": "Harborview Consulting",
        "client_email": "accounts@harborview.com",
        "relationship_info": "standard client",
        "outstanding_amount": 5500.00,
        "due_date": "2025-08-20",
    },
    # Tone 4 — courteous: new client
    "INV-005": {
        "invoice_number": "INV-005",
        "client_name": "Apex Innovations Inc",
        "client_email": "finance@apex-innovations.com",
        "relationship_info": "new client",
        "outstanding_amount": 3100.00,
        "due_date": "2025-09-01",
    },
    # Tone 5 — polite: high value client
    "INV-006": {
        "invoice_number": "INV-006",
        "client_name": "Sterling Global Partners",
        "client_email": "treasury@sterling-global.com",
        "relationship_info": "high value client",
        "outstanding_amount": 125000.00,
        "due_date": "2025-09-15",
    },
    # Tone 5 — polite: long-term client
    "INV-007": {
        "invoice_number": "INV-007",
        "client_name": "Evergreen Tech Solutions",
        "client_email": "billing@evergreen-tech.com",
        "relationship_info": "long-term client",
        "outstanding_amount": 22400.00,
        "due_date": "2025-09-30",
    },
    # Tone 2/3 — mid-range: overdue no defaults, moderate amount
    "INV-008": {
        "invoice_number": "INV-008",
        "client_name": "Cascade Digital Services",
        "client_email": "invoices@cascade-digital.com",
        "relationship_info": "overdue, no prior defaults",
        "outstanding_amount": 6750.00,
        "due_date": "2025-08-10",
    },
}


def fetch_client(invoice_number: str) -> Optional[ClientRecord]:
    """Return the client record for the given invoice number, or None if not found."""
    return CRM_DATA.get(invoice_number)
