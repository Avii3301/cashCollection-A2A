"""
routes/a2a.py — A2A JSON-RPC 2.0 endpoint.

    POST /a2a  — Accepts tasks/send and tasks/get method calls.

See a2a/task_handler.py for the full protocol implementation.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from a2a.task_handler import handle_jsonrpc

router = APIRouter(tags=["A2A"])

_EXAMPLE = {
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


@router.post(
    "/a2a",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": _EXAMPLE,
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
    return JSONResponse(handle_jsonrpc(payload))
