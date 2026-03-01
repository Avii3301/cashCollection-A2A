"""
routes/draft.py — POST /draft endpoint.

Processes a batch of invoices:
  1. Runs the CrewAI pipeline (CRM fetch → tone analysis → email draft)
  2. Scores each email (tone consistency, completeness, guardrail)
  3. Logs all results to a single MLflow run for the batch,
     with per-invoice metric names: INV-001/tone_consistency, etc.
"""

import logging
from datetime import datetime, timezone

import mlflow
from mlflow.tracking import MlflowClient
from fastapi import APIRouter

from crew.email_crew import run_for_invoice
from evaluation.scorers import run_scorers
from models import DraftError, DraftRequest, DraftResponse, DraftResult

router = APIRouter(tags=["Email Drafting"])
logger = logging.getLogger(__name__)


@router.post("/draft", response_model=DraftResponse)
def draft_emails(request: DraftRequest):
    """
    Process a batch of invoices and draft collection emails.

    For each invoice:
    1. Fetches client data from CRM via MCP tool
    2. Determines communication tone (0-5 scale)
    3. Drafts email with subject and description
    4. Scores the output (tone consistency, completeness, guardrail)

    All invoices in a batch share one MLflow run. Metrics are namespaced
    per invoice: `INV-001/tone_consistency`, `INV-001/completeness_overall`, etc.

    Returns drafted emails or per-invoice errors.
    """
    results: list[DraftResult] = []
    errors: list[DraftError] = []

    batch_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mlflow_client = MlflowClient()

    with mlflow.start_run(run_name=f"batch-{batch_ts}") as batch_run:
        run_id = batch_run.info.run_id
        mlflow.log_param("batch_size", len(request.invoices))

        for invoice_input in request.invoices:
            inv_dict = invoice_input.model_dump()
            inv_num = inv_dict["invoice_number"]

            try:
                result = run_for_invoice(inv_dict)
                scores = run_scorers(result)

                # Per-invoice params — all land on the same run, prefixed by invoice number
                mlflow.log_params({
                    f"{inv_num}/company": inv_dict["company_name"],
                    f"{inv_num}/amount": inv_dict["amount"],
                    f"{inv_num}/due_date": inv_dict["due_date"],
                    f"{inv_num}/tone_score": result.get("tone_score", -1),
                })

                # Log scorer metrics directly by run ID so mlflow.crewai.autolog()
                # cannot interfere with the active run context during crew.kickoff().
                for score in scores:
                    value = score.get("value")
                    if value is None:
                        continue
                    try:
                        mlflow_client.log_metric(run_id, f"{inv_num}/{score['name']}", float(value))
                    except Exception as exc:
                        logger.warning("Failed to log metric %s/%s: %s", inv_num, score["name"], exc)

                results.append(DraftResult(**result))
                logger.info("✓ %s (tone=%s) — %d scores logged", inv_num, result.get("tone_score"), len(scores))

            except Exception as exc:
                logger.exception("✗ Failed to process invoice %s", inv_num)
                mlflow.log_param(f"{inv_num}/error", str(exc)[:250])
                errors.append(DraftError(invoice_number=inv_num, error=str(exc)))

        mlflow.log_metrics({
            "invoices_processed": len(results),
            "invoices_errored": len(errors),
        })

    return DraftResponse(results=results, errors=errors)
