from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InvoiceLineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class InvoiceExtraction(BaseModel):
    vendor: str | None = None
    payable_to: str | None = None
    remittance_address: str | None = None
    billing_address: str | None = None
    physical_billing_address: str | None = None
    service_address: str | None = None
    account_number: str | None = None
    friendly_name: str | None = None
    name_on_account: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    amount_due: float | None = None
    previous_amount_due: float | None = None
    previous_payment_date: str | None = None
    previous_payment_amount: float | None = None
    currency: str | None = "USD"
    payment_terms: str | None = None
    po_box: str
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    confidence: float = 0.0
    raw_text_excerpt: str | None = None
    model_source: str = "heuristic"
    learning_hints: dict[str, Any] = Field(default_factory=dict)


class VerificationIssue(BaseModel):
    severity: str
    field: str
    message: str


class VerificationResult(BaseModel):
    status: str
    score: float
    issues: list[VerificationIssue] = Field(default_factory=list)


class OcrWord(BaseModel):
    text: str
    normalized_text: str
    page_number: int
    left: int
    top: int
    width: int
    height: int
    confidence: float


class OcrPage(BaseModel):
    page_number: int
    width: int
    height: int
    text: str
    words: list[OcrWord] = Field(default_factory=list)


class OcrResult(BaseModel):
    text: str
    page_count: int
    pages: list[OcrPage] = Field(default_factory=list)


class IngestionResult(BaseModel):
    status: str
    attempts: int = 0
    last_attempt_at: str | None = None
    ingested_at: str | None = None
    error_message: str | None = None


class DocumentBundle(BaseModel):
    document_id: str
    po_box: str
    status: str
    extraction: InvoiceExtraction
    verification: VerificationResult
    alignment: dict[str, Any]
    ingestion: IngestionResult = Field(default_factory=lambda: IngestionResult(status="pending"))
