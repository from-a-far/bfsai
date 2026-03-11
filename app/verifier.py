from __future__ import annotations

from math import fabs
from typing import Any

from .config import Settings
from .schemas import InvoiceExtraction, VerificationIssue, VerificationResult


class Verifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def verify(self, extraction: InvoiceExtraction, hints: dict[str, Any]) -> VerificationResult:
        issues: list[VerificationIssue] = []
        if not extraction.vendor:
            issues.append(VerificationIssue(severity="high", field="vendor", message="Vendor is missing"))
        if not extraction.invoice_number:
            issues.append(VerificationIssue(severity="medium", field="invoice_number", message="Invoice number is missing"))
        if not extraction.invoice_date:
            issues.append(VerificationIssue(severity="medium", field="invoice_date", message="Invoice date is missing"))
        payable_total = extraction.amount_due if extraction.amount_due is not None else extraction.total
        if payable_total is None:
            issues.append(VerificationIssue(severity="high", field="total", message="Total is missing"))
        if payable_total and payable_total >= self.settings.thresholds.flagged_amount:
            issues.append(
                VerificationIssue(
                    severity="medium",
                    field="total",
                    message=f"Total exceeds configured threshold of {self.settings.thresholds.flagged_amount:.2f}",
                )
            )
        if extraction.subtotal is not None and extraction.tax is not None and payable_total is not None:
            delta = fabs((extraction.subtotal + extraction.tax) - payable_total)
            if delta > self.settings.thresholds.total_delta_tolerance:
                issues.append(
                    VerificationIssue(
                        severity="high",
                        field="total",
                        message=f"Subtotal + tax differs from total by {delta:.2f}",
                    )
                )
        if extraction.line_items and payable_total is not None:
            line_sum = sum(item.amount or 0 for item in extraction.line_items)
            if fabs(line_sum - payable_total) > self.settings.thresholds.total_delta_tolerance:
                issues.append(
                    VerificationIssue(
                        severity="medium",
                        field="line_items",
                        message=f"Line-item total differs from invoice total by {fabs(line_sum - payable_total):.2f}",
                    )
                )
        if extraction.amount_due is not None and extraction.total is not None:
            delta = fabs(extraction.amount_due - extraction.total)
            if delta > self.settings.thresholds.total_delta_tolerance:
                issues.append(
                    VerificationIssue(
                        severity="low",
                        field="amount_due",
                        message=f"Amount due differs from total by {delta:.2f}",
                    )
                )
        hinted_currency = hints.get("confirmed_fields", {}).get("currency")
        if hinted_currency and extraction.currency != hinted_currency:
            issues.append(
                VerificationIssue(
                    severity="low",
                    field="currency",
                    message=f"Currency differs from confirmed vendor profile ({hinted_currency})",
                )
            )

        penalty = sum({"low": 0.03, "medium": 0.08, "high": 0.15}[issue.severity] for issue in issues)
        score = max(0.0, round(extraction.confidence - penalty, 2))
        status = "verified" if score >= self.settings.thresholds.verified_confidence and not any(issue.severity == "high" for issue in issues) else "review"
        return VerificationResult(status=status, score=score, issues=issues)
