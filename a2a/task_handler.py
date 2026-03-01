"""
task_handler.py — A2A JSON-RPC 2.0 task handler.

Supported methods:
    tasks/send  — Submit a batch of invoices for email drafting
    tasks/get   — Retrieve the current state of a task by ID

Task state machine:
    submitted → working → completed
                        → failed (if all invoices errored)

The in-memory task store is sufficient for demo purposes.
For production, replace with Redis or a database-backed store.

A2A message input format expected under tasks/send:
{
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
                                "invoice_number": "INV-001",
                                "company_name": "Acme Corp",
                                "amount": 12500.00,
                                "due_date": "2025-09-15"
                            }
                        ]
                    }
                }
            ]
        }
    }
}
"""

import logging
import uuid
from typing import Any

from crew.email_crew import run_for_invoice

logger = logging.getLogger(__name__)

# In-memory task store: task_id → TaskState dict
_task_store: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------
def handle_jsonrpc(payload: dict) -> dict:
    """
    Route an A2A JSON-RPC 2.0 request to the appropriate handler.

    Args:
        payload: Parsed JSON-RPC request dict

    Returns:
        JSON-RPC 2.0 response dict
    """
    rpc_id = payload.get("id")

    if payload.get("jsonrpc") != "2.0":
        return _error_response(rpc_id, -32600, "Invalid Request: jsonrpc must be '2.0'")

    method = payload.get("method")
    params = payload.get("params", {})

    if method == "tasks/send":
        return _tasks_send(rpc_id, params)
    elif method == "tasks/get":
        return _tasks_get(rpc_id, params)
    else:
        return _error_response(rpc_id, -32601, f"Method not found: '{method}'")


# ---------------------------------------------------------------------------
# tasks/send
# ---------------------------------------------------------------------------
def _tasks_send(rpc_id: Any, params: dict) -> dict:
    """
    Handle tasks/send: extract invoices from the A2A message and process the batch.

    Expected params structure:
        params.message.parts[0].data.invoices: list[dict]
    """
    task_id = str(uuid.uuid4())

    # ── Extract invoice list from A2A message parts ───────────────────────
    try:
        parts = params["message"]["parts"]
        data = next(
            (p["data"] for p in parts if isinstance(p, dict) and "data" in p),
            None,
        )
        if data is None:
            raise ValueError("No 'data' part found in message parts")
        invoices: list[dict] = data["invoices"]
        if not isinstance(invoices, list) or len(invoices) == 0:
            raise ValueError("'invoices' must be a non-empty list")
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        return _error_response(rpc_id, -32602, f"Invalid params: {exc}")

    # ── Initialise task: submitted ────────────────────────────────────────
    _task_store[task_id] = {
        "id": task_id,
        "status": {"state": "submitted"},
        "artifacts": [],
    }

    # ── Transition: working ───────────────────────────────────────────────
    _task_store[task_id]["status"]["state"] = "working"
    logger.info("A2A task %s started — processing %d invoice(s)", task_id, len(invoices))

    results: list[dict] = []
    errors: list[dict] = []

    for invoice in invoices:
        inv_num = invoice.get("invoice_number", "<unknown>")
        try:
            result = run_for_invoice(invoice)
            results.append(result)
            logger.info("A2A task %s — ✓ processed %s", task_id, inv_num)
        except Exception as exc:
            logger.exception("A2A task %s — ✗ failed %s", task_id, inv_num)
            errors.append({"invoice_number": inv_num, "error": str(exc)})

    # ── Transition: completed / failed ────────────────────────────────────
    final_state = "failed" if (errors and not results) else "completed"
    _task_store[task_id]["status"]["state"] = final_state
    _task_store[task_id]["artifacts"] = [
        {
            "name": "drafted_emails",
            "mimeType": "application/json",
            "parts": [
                {
                    "type": "data",
                    "data": {
                        "results": results,
                        "errors": errors,
                    },
                }
            ],
        }
    ]

    logger.info(
        "A2A task %s %s — %d drafted, %d errored",
        task_id, final_state, len(results), len(errors),
    )

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": _task_store[task_id],
    }


# ---------------------------------------------------------------------------
# tasks/get
# ---------------------------------------------------------------------------
def _tasks_get(rpc_id: Any, params: dict) -> dict:
    """
    Handle tasks/get: retrieve the current state of a task.

    Expected params: {"id": "<task_id>"}
    """
    task_id = params.get("id")
    if not task_id:
        return _error_response(rpc_id, -32602, "Missing required param 'id'")
    if task_id not in _task_store:
        return _error_response(rpc_id, -32602, f"Task not found: '{task_id}'")

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": _task_store[task_id],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _error_response(rpc_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message},
    }
