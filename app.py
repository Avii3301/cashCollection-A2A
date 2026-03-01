"""
app.py — FastAPI serving application for the Cash Collection Email Drafter.

Endpoints:
    GET  /health                     — Health check
    GET  /.well-known/agent.json     — A2A Agent Card
    POST /a2a                        — A2A JSON-RPC 2.0 task endpoint
    POST /draft                      — Batch invoice processing (direct API)

MLflow:
    Tracing is configured at startup via mlflow.crewai.autolog().
    Each invoice processed by /draft gets a nested MLflow run with
    tone_consistency, completeness_*, and guardrail_pass metrics logged.

Run:
    uvicorn app:app --reload --port 8000
"""

import logging
import os
from contextlib import asynccontextmanager

import mlflow
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from a2a.agent_card import build_agent_card
from a2a.task_handler import handle_jsonrpc
from crew.email_crew import run_for_invoice
from evaluation.scorers import log_scores_to_mlflow, run_scorers

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()  # load .env if present

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# MLflow setup
# ---------------------------------------------------------------------------
def _setup_mlflow() -> None:
    """Configure MLflow tracking and enable CrewAI autolog."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "databricks")
    mlflow.set_tracking_uri(tracking_uri)

    experiment_name = os.environ.get(
        "MLFLOW_EXPERIMENT_NAME",
        "cash-collection-drafter",
    )

    try:
        mlflow.set_experiment(experiment_name)
        logger.info("MLflow experiment: %s", experiment_name)
    except Exception as exc:
        logger.warning(
            "Could not set MLflow experiment '%s': %s. "
            "Tracking will use the default experiment.",
            experiment_name,
            exc,
        )

    try:
        mlflow.crewai.autolog()
        logger.info("MLflow CrewAI autolog enabled")
    except Exception as exc:
        logger.warning("MLflow crewai autolog not available: %s", exc)

    logger.info("MLflow tracking URI: %s", tracking_uri)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_mlflow()
    print(f"\n  ✓ Service ready at: {BASE_URL}\n  ✓ Interactive docs: {BASE_URL}/docs\n", flush=True)
    yield
    logger.info("Cash Collection Email Drafter shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Cash Collection Email Drafter",
    description=(
        "CrewAI-powered service that drafts collection emails for invoice batches. "
        "Fetches CRM data via MCP, decides tone (0-5), and produces subject + body."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class InvoiceInput(BaseModel):
    invoice_number: str = Field(..., examples=["INV-001"])
    company_name: str = Field(..., examples=["Acme Corp"])
    amount: float = Field(..., gt=0, examples=[12500.00])
    due_date: str = Field(..., examples=["2025-09-15"])


class DraftRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "invoices": [
                    {
                        "invoice_number": "INV-001",
                        "company_name": "Blackstone Retail Ltd",
                        "amount": 47500.00,
                        "due_date": "2025-07-01",
                    },
                    {
                        "invoice_number": "INV-006",
                        "company_name": "Sterling Global Partners",
                        "amount": 125000.00,
                        "due_date": "2025-09-15",
                    },
                ]
            }
        }
    )
    invoices: list[InvoiceInput] = Field(..., min_length=1)


class DraftResult(BaseModel):
    invoice_number: str
    tone_score: int
    subject: str
    description: str


class DraftError(BaseModel):
    invoice_number: str
    error: str


class DraftResponse(BaseModel):
    results: list[DraftResult]
    errors: list[DraftError]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["System"])
def health():
    """Service health check."""
    return {"status": "ok", "service": "cash-collection-drafter", "version": "1.0.0"}


@app.get("/.well-known/agent.json", tags=["A2A"])
def agent_card():
    """
    A2A Agent Card — describes this agent's capabilities and skills.
    Served at the standard well-known path per the A2A specification.
    """
    return JSONResponse(build_agent_card(BASE_URL))


_A2A_EXAMPLE = {
    "jsonrpc": "2.0",
    "id": "req-1",
    "method": "tasks/send",
    "params": {
        "message": {
            "parts": [
                {
                    "type": "data",
                    "data": {
                        "invoices": [
                            {
                                "invoice_number": "INV-004",
                                "company_name": "Harborview Consulting",
                                "amount": 5500.00,
                                "due_date": "2025-08-20",
                            }
                        ]
                    },
                }
            ]
        }
    },
}


@app.post(
    "/a2a",
    tags=["A2A"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": _A2A_EXAMPLE,
                }
            },
        }
    },
)
async def a2a_endpoint(request: Request):
    """
    A2A JSON-RPC 2.0 task endpoint.

    Accepts tasks/send and tasks/get method calls.
    See a2a/task_handler.py for the full protocol implementation.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: invalid JSON"},
            },
        )
    response = handle_jsonrpc(payload)
    return JSONResponse(response)


@app.post("/draft", response_model=DraftResponse, tags=["Email Drafting"])
def draft_emails(request: DraftRequest):
    """
    Process a batch of invoices and draft collection emails.

    For each invoice:
    1. Fetches client data from CRM via MCP tool
    2. Determines communication tone (0-5 scale)
    3. Drafts email with subject and description
    4. Logs MLflow metrics (tone_consistency, completeness_*, guardrail_pass)

    Returns drafted emails or per-invoice errors.
    """
    results: list[DraftResult] = []
    errors: list[DraftError] = []

    with mlflow.start_run(run_name="draft-batch"):
        mlflow.log_param("batch_size", len(request.invoices))

        for invoice_input in request.invoices:
            inv_dict = invoice_input.model_dump()
            inv_num = inv_dict["invoice_number"]

            with mlflow.start_run(run_name=f"draft-{inv_num}", nested=True):
                mlflow.log_params({
                    "invoice_number": inv_num,
                    "company_name": inv_dict["company_name"],
                    "amount": inv_dict["amount"],
                    "due_date": inv_dict["due_date"],
                })

                try:
                    result = run_for_invoice(inv_dict)

                    # Run evaluation scorers and log to MLflow
                    scores = run_scorers(result)
                    log_scores_to_mlflow(scores)

                    # Log tone_score as a param for easy filtering in Databricks
                    mlflow.log_param("tone_score", result.get("tone_score", -1))

                    results.append(DraftResult(**result))
                    logger.info("✓ Drafted email for %s (tone=%s)", inv_num, result.get("tone_score"))

                except Exception as exc:
                    logger.exception("✗ Failed to process invoice %s", inv_num)
                    mlflow.log_param("error", str(exc)[:250])
                    errors.append(DraftError(invoice_number=inv_num, error=str(exc)))

        mlflow.log_metrics({
            "invoices_processed": len(results),
            "invoices_errored": len(errors),
        })

    return DraftResponse(results=results, errors=errors)
