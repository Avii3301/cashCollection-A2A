"""
mcp_server.py — FastMCP server exposing the CRM lookup tool.

The `mcp` FastMCP server object is imported in-process by email_crew.py
via fastmcp.Client. It can also be run standalone via STDIO for external
agent-to-agent use.

Usage (standalone STDIO):
    python mcp_server.py
"""

from fastmcp import FastMCP
from crm import fetch_client

mcp = FastMCP(
    name="CRM Tool Server",
    instructions=(
        "You are a CRM data access server. Use fetch_client_by_invoice to retrieve "
        "client records by invoice number."
    ),
)


@mcp.tool()
def fetch_client_by_invoice(invoice_number: str) -> dict:
    """Fetch a client's CRM record given an invoice number.

    Looks up the client associated with the provided invoice number in the CRM
    system and returns their full profile including relationship information,
    outstanding amount, and due date.

    Args:
        invoice_number: The invoice identifier (e.g. 'INV-001').

    Returns:
        A dict with fields:
            - invoice_number (str)
            - client_name (str)
            - client_email (str)
            - relationship_info (str): e.g. 'high value client', 'repeat late payer'
            - outstanding_amount (float)
            - due_date (str): ISO 8601 date string
        Or an error dict: {"error": "<message>"} if the invoice is not found.
    """
    record = fetch_client(invoice_number)
    if record is None:
        return {"error": f"No CRM record found for invoice number '{invoice_number}'"}
    return dict(record)


if __name__ == "__main__":
    # STDIO transport — blocks and reads from stdin, writes to stdout
    mcp.run()
