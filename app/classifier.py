from __future__ import annotations

import re
from dataclasses import dataclass


AMOUNT_DUE_PATTERNS = [
    r"\bamount due\b",
    r"\bbalance due\b",
    r"\bpayment due\b",
    r"\btotal due\b",
    r"\bminimum payment due\b",
]

DOCUMENT_TYPE_RULES = (
    ("cc", ("credit card", "cardmember", "statement closing date", "available credit", "minimum payment due")),
    ("t", ("form 1099", "w-2", "schedule c", "schedule e", "irs", "tax year", "internal revenue service")),
    ("i", ("policy number", "insured", "coverage", "premium", "declarations page")),
    ("d", ("deposit slip", "deposit ticket", "cash deposit", "check deposit")),
    ("p", ("payroll", "pay stub", "earnings statement", "employee earnings")),
    ("s", ("statement period", "account summary", "beginning balance", "ending balance", "statement date")),
    ("r", ("report", "summary report", "monthly report")),
)

@dataclass(frozen=True, slots=True)
class DocumentRouting:
    dtype: str
    should_extract: bool
    duplicate_to_type_folder: bool
    reason: str


def classify_document(text: str) -> DocumentRouting:
    normalized = " ".join(text.lower().split())
    amount_due_detected = any(re.search(pattern, normalized) for pattern in AMOUNT_DUE_PATTERNS)

    matched_dtype = None
    matched_reason = "defaulted to bill because amount due was detected" if amount_due_detected else "no document type matched"
    for dtype, keywords in DOCUMENT_TYPE_RULES:
        if any(keyword in normalized for keyword in keywords):
            matched_dtype = dtype
            matched_reason = f"matched {dtype} keyword"
            break

    if matched_dtype in {"cc", "t"}:
        return DocumentRouting(
            dtype=matched_dtype,
            should_extract=matched_dtype == "cc",
            duplicate_to_type_folder=matched_dtype == "cc",
            reason=matched_reason,
        )

    if amount_due_detected:
        return DocumentRouting(
            dtype="b" if matched_dtype not in {"cc"} else matched_dtype,
            should_extract=True,
            duplicate_to_type_folder=matched_dtype == "cc",
            reason=matched_reason,
        )

    if matched_dtype:
        return DocumentRouting(
            dtype=matched_dtype,
            should_extract=False,
            duplicate_to_type_folder=False,
            reason=matched_reason,
        )

    return DocumentRouting(dtype="other", should_extract=False, duplicate_to_type_folder=False, reason=matched_reason)
