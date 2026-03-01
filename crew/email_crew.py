"""
email_crew.py — Core CrewAI crew for collection email drafting.

Public API:
    run_for_invoice(invoice: dict) -> dict
        Processes a single invoice dict and returns:
        {
            "invoice_number": str,
            "tone_score": int,        # 0-5
            "subject": str,
            "description": str
        }

The crew runs three sequential tasks:
    1. CRM Fetcher   → calls fetch_client_by_invoice via FastMCP in-process client
    2. Tone Analyzer → decides tone score (0-5) from rubric
    3. Email Drafter → drafts email with subject + description
"""

import asyncio
import json
import logging
import re
from typing import Type

from crewai import Agent, Crew, LLM, Process, Task
from crewai.tools import BaseTool
from fastmcp import Client
from pydantic import BaseModel, Field

from crew.tone_rubric import TONE_RUBRIC

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRM tool — FastMCP in-process client (no subprocess, no crewai_tools)
# ---------------------------------------------------------------------------
class _InvoiceInput(BaseModel):
    invoice_number: str = Field(
        ..., description="Invoice number to look up, e.g. 'INV-001'"
    )


class FetchClientTool(BaseTool):
    """
    CrewAI tool that calls the FastMCP CRM server in-process.
    Connects directly to the mcp_server FastMCP object — no subprocess needed.
    """

    name: str = "fetch_client_by_invoice"
    description: str = (
        "Fetch the complete client CRM record for a given invoice number. "
        "Returns client name, email, relationship info, outstanding amount, and due date."
    )
    args_schema: Type[BaseModel] = _InvoiceInput

    def _run(self, invoice_number: str) -> str:
        async def _call() -> str:
            # Import the FastMCP server object in-process (cached after first call)
            from mcp_server import mcp as _mcp_server  # noqa: PLC0415

            async with Client(_mcp_server) as client:
                result = await client.call_tool(
                    "fetch_client_by_invoice",
                    {"invoice_number": invoice_number},
                )
            # fastmcp 2.x: call_tool returns CallToolResult; text is in .content
            content = getattr(result, "content", result)
            if not content:
                return "{}"
            item = content[0] if isinstance(content, list) else content
            return item.text if hasattr(item, "text") else str(item)

        return asyncio.run(_call())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_for_invoice(invoice: dict) -> dict:
    """
    Run the full three-agent crew for a single invoice.

    Args:
        invoice: dict with keys invoice_number, company_name, amount, due_date

    Returns:
        dict with keys: invoice_number, tone_score, subject, description
    """
    invoice_number: str = invoice["invoice_number"]
    company_name: str = invoice["company_name"]
    amount = invoice["amount"]
    due_date: str = invoice["due_date"]

    llm = LLM(model="gpt-4o-mini", temperature=0.3)
    crm_tool = FetchClientTool()

    # ── Agent 1: CRM Fetcher ──────────────────────────────────────────
    crm_fetcher = Agent(
        role="CRM Data Retrieval Specialist",
        goal="Fetch the complete client record for a given invoice number using the CRM tool.",
        backstory=(
            "You are a data retrieval specialist responsible for fetching client "
            "records from the company CRM system. You use the fetch_client_by_invoice "
            "tool to retrieve accurate client data and always return the full record "
            "without modification. If no record is found, return the error response as-is."
        ),
        tools=[crm_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    # ── Agent 2: Tone Analyzer ────────────────────────────────────────
    tone_analyzer = Agent(
        role="Communication Tone Strategist",
        goal=(
            "Determine the most appropriate email tone score (0-5) for a collection "
            "email based on client data and relationship context."
        ),
        backstory=(
            "You are a senior communication strategist with deep expertise in "
            "accounts receivable and client relationship management. You apply a "
            "structured rubric to determine how firm or polite a collection email "
            "should be. You always output a JSON object with tone_score (int 0-5) "
            "and reasoning (one sentence)."
        ),
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    # ── Agent 3: Email Drafter ────────────────────────────────────────
    email_drafter = Agent(
        role="Collections Email Specialist",
        goal=(
            "Draft a professional, structured collection email that exactly matches "
            "the decided tone score and contains all required structural elements."
        ),
        backstory=(
            "You are a senior accounts receivable specialist with years of experience "
            "drafting collection emails across a wide spectrum of client relationships. "
            "Your emails are always professional, clearly structured, and precisely "
            "calibrated to the tone score provided. You never include commentary or "
            "preamble — only the requested JSON output."
        ),
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    # ── Task 1: Fetch CRM Data ────────────────────────────────────────
    fetch_task = Task(
        description=(
            "Use the fetch_client_by_invoice tool to retrieve the client record "
            f"for invoice number '{invoice_number}'. "
            "Return the complete record exactly as returned by the tool."
        ),
        expected_output=(
            "A dict containing: invoice_number, client_name, client_email, "
            "relationship_info, outstanding_amount, due_date. "
            "If not found, the error dict from the tool."
        ),
        agent=crm_fetcher,
    )

    # ── Task 2: Analyze Tone ──────────────────────────────────────────
    analyze_task = Task(
        description=(
            "Using the client record from the previous task and the tone rubric below, "
            f"determine the appropriate tone score (integer 0-5) for the collection "
            f"email for invoice '{invoice_number}'.\n\n"
            f"{TONE_RUBRIC}\n\n"
            'Respond with ONLY a valid JSON object (no markdown, no code fences):\n'
            '{"tone_score": <int 0-5>, "reasoning": "<one concise sentence>"}'
        ),
        expected_output='{"tone_score": <int>, "reasoning": "<str>"}',
        agent=tone_analyzer,
        context=[fetch_task],
    )

    # ── Task 3: Draft Email ───────────────────────────────────────────
    draft_task = Task(
        description=(
            "Using the CRM data and tone score from previous tasks, draft a "
            f"collection email for invoice {invoice_number} issued to "
            f"{company_name} for ${amount:,.2f} due on {due_date}.\n\n"
            "The email MUST contain all four structural elements:\n"
            "1. Greeting — addressed to client_name from the CRM record\n"
            "2. Body — mentions invoice_number, amount ($), and due_date\n"
            "3. Call to action — specific payment deadline and payment instructions\n"
            "4. Professional sign-off — from the Accounts Receivable team\n\n"
            "Tone calibration:\n"
            "  tone_score 0 = firm/strict (state consequences, 48-hour deadline)\n"
            "  tone_score 5 = warm and polite (relationship-first, appreciative language)\n\n"
            "You MUST output a valid JSON object with EXACTLY these two fields "
            "(no markdown, no code fences, no preamble):\n"
            '{"subject": "<concise subject line referencing invoice_number and company_name>", '
            '"description": "<complete email body with all four structural elements>"}'
        ),
        expected_output=(
            'Valid JSON: {"subject": "<str>", "description": "<complete email text>"}'
        ),
        agent=email_drafter,
        context=[fetch_task, analyze_task],
    )

    # ── Assemble & Run ────────────────────────────────────────────────
    crew = Crew(
        agents=[crm_fetcher, tone_analyzer, email_drafter],
        tasks=[fetch_task, analyze_task, draft_task],
        process=Process.sequential,
        verbose=True,
    )

    crew.kickoff(
        inputs={
            "invoice_number": invoice_number,
            "company_name": company_name,
            "amount": f"{amount:,.2f}",
            "due_date": due_date,
        }
    )

    # ── Parse outputs ─────────────────────────────────────────────────
    tone_score = _extract_tone_score(analyze_task.output)
    subject, description = _extract_email_parts(draft_task.output)

    return {
        "invoice_number": invoice_number,
        "tone_score": tone_score,
        "subject": subject,
        "description": description,
    }


# ---------------------------------------------------------------------------
# Output parsers
# ---------------------------------------------------------------------------
def _extract_tone_score(task_output) -> int:
    """
    Parse tone_score from the Tone Analyzer task output.
    Handles: plain JSON, JSON in markdown code fences.
    Returns -1 if parsing fails.
    """
    try:
        raw = _raw_str(task_output)
        raw = _strip_code_fences(raw)
        data = json.loads(raw)
        return int(data["tone_score"])
    except Exception:
        # Regex fallback
        raw = _raw_str(task_output)
        match = re.search(r'"tone_score"\s*:\s*(\d)', raw)
        if match:
            return int(match.group(1))
        logger.warning("Could not parse tone_score from: %s", raw[:200])
        return -1


def _extract_email_parts(task_output) -> tuple[str, str]:
    """
    Parse subject and description from the Email Drafter task output.
    Handles: plain JSON, JSON in markdown code fences.
    Returns ("", "") if parsing fails.
    """
    try:
        raw = _raw_str(task_output)
        raw = _strip_code_fences(raw)
        data = json.loads(raw)
        return str(data.get("subject", "")), str(data.get("description", ""))
    except Exception:
        # Best-effort extraction using regex
        raw = _raw_str(task_output)
        subject_match = re.search(r'"subject"\s*:\s*"([^"]+)"', raw)
        desc_match = re.search(r'"description"\s*:\s*"(.*?)(?<!\\)"', raw, re.DOTALL)
        subject = subject_match.group(1) if subject_match else ""
        description = desc_match.group(1).replace('\\"', '"') if desc_match else raw
        logger.warning("Fell back to regex for email parts extraction")
        return subject, description


def _raw_str(task_output) -> str:
    """Extract the raw string from a CrewAI TaskOutput object or plain string."""
    if hasattr(task_output, "raw"):
        return str(task_output.raw)
    return str(task_output)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ``` or ``` ... ```)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    return text.strip()
