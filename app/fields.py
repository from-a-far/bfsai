from __future__ import annotations

from dataclasses import dataclass

from .utils import as_float


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    label: str
    kind: str = "text"


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("vendor", "Payee/Vendor Name"),
    FieldSpec("payable_to", "Payable To"),
    FieldSpec("remittance_address", "Remittance Address"),
    FieldSpec("billing_address", "Billing Address"),
    FieldSpec("physical_billing_address", "Physical Billing Address"),
    FieldSpec("service_address", "Service Address"),
    FieldSpec("account_number", "Account Number"),
    FieldSpec("friendly_name", "Friendly Name"),
    FieldSpec("name_on_account", "Name On Account"),
    FieldSpec("invoice_number", "Invoice Number"),
    FieldSpec("invoice_date", "Invoice Date", "date"),
    FieldSpec("due_date", "Due Date", "date"),
    FieldSpec("subtotal", "Subtotal", "amount"),
    FieldSpec("tax", "Tax", "amount"),
    FieldSpec("total", "Total", "amount"),
    FieldSpec("amount_due", "Amount Due", "amount"),
    FieldSpec("currency", "Currency"),
    FieldSpec("payment_terms", "Payment Terms"),
    FieldSpec("previous_amount_due", "Previous Amount Due", "amount"),
    FieldSpec("previous_payment_date", "Previous Payment Date", "date"),
    FieldSpec("previous_payment_amount", "Previous Payment Amount", "amount"),
)

FIELD_SPECS_BY_NAME = {field.name: field for field in FIELD_SPECS}
TRACKED_FIELDS = tuple(field.name for field in FIELD_SPECS)
AMOUNT_FIELDS = {field.name for field in FIELD_SPECS if field.kind == "amount"}
DATE_FIELDS = {field.name for field in FIELD_SPECS if field.kind == "date"}


def serialize_field_specs() -> list[dict[str, str]]:
    return [{"name": field.name, "label": field.label, "kind": field.kind} for field in FIELD_SPECS]


def coerce_field_value(field_name: str, value: str | None) -> str | float | None:
    if value in (None, ""):
        return None
    if field_name in AMOUNT_FIELDS:
        return as_float(value)
    return str(value).strip()
