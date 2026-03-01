"""
models.py — Pydantic request / response models for the Cash Collection API.
"""

from pydantic import BaseModel, ConfigDict, Field


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
