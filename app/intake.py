from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from pathlib import Path

from .client_lookup import ClientLookupService, ClientMatch
from .classifier import classify_document
from .config import Settings
from .documents import (
    SUPPORTED_EXTENSIONS,
    copy_client_new_to_document_type,
    known_client_layout,
    move_scan_to_client_document_type,
    move_scan_to_client_new,
    move_scan_to_client_other,
)
from .extractor import Extractor
from .payee_lookup import PayeeAccountLookupService
from .repository import Repository
from .schemas import InvoiceExtraction
from .utils import as_float, detect_po_box, normalize_text, normalize_vendor


UNRESOLVED_CLIENT_KEY = "unresolved"


class ScanIntakeService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.extractor = Extractor(settings)
        self.client_lookup = ClientLookupService(settings)
        self.payee_lookup = PayeeAccountLookupService(settings)

    def process_scan(self, source_path: Path, forced_client_key: str | None = None) -> Path | None:
        if not source_path.exists() or not source_path.is_file():
            return None

        client_key = forced_client_key or UNRESOLVED_CLIENT_KEY
        if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return move_scan_to_client_other(self.settings, client_key, source_path)

        try:
            ocr_result = self.extractor.read_text(source_path)
        except Exception:
            return move_scan_to_client_other(self.settings, client_key, source_path)
        if not forced_client_key:
            matched_client = self.suggest_client(source_path, ocr_result.text)
            if matched_client and known_client_layout(self.settings, matched_client.po_box):
                client_key = matched_client.po_box

        routing = classify_document(ocr_result.text)
        if routing.should_extract:
            new_path = move_scan_to_client_new(self.settings, client_key, source_path)
            if routing.duplicate_to_type_folder:
                copy_client_new_to_document_type(self.settings, client_key, new_path, routing.dtype)
            return new_path
        if routing.dtype != "other":
            return move_scan_to_client_document_type(self.settings, client_key, source_path, routing.dtype)
        return move_scan_to_client_other(self.settings, client_key, source_path)

    def suggest_client(self, source_path: Path, ocr_text: str | None = None) -> ClientMatch | None:
        text = ocr_text
        if text is None:
            try:
                text = self.extractor.read_text(source_path).text
            except Exception:
                return None

        detected_po_box = detect_po_box(text)
        if detected_po_box and known_client_layout(self.settings, detected_po_box):
            client_name = self.client_lookup.client_name_for_po_box(detected_po_box) or detected_po_box
            return ClientMatch(
                po_box=detected_po_box,
                client_id=0,
                client_name=client_name,
                matched_alias=f"po box {detected_po_box}",
                score=500,
            )

        matched_client = self.client_lookup.match_po_box(text)
        if matched_client and known_client_layout(self.settings, matched_client.po_box):
            return matched_client

        try:
            extraction = self.extractor.extract(source_path)
        except Exception:
            return None

        payee_match = self.payee_lookup.match_client(extraction)
        if payee_match and known_client_layout(self.settings, payee_match.po_box):
            return payee_match

        history_match = self._match_client_from_history(extraction)
        if history_match and known_client_layout(self.settings, history_match.po_box):
            return history_match
        return None

    def _match_client_from_history(self, extraction: InvoiceExtraction) -> ClientMatch | None:
        vendor = normalize_vendor(extraction.vendor or extraction.payable_to)
        account_number = self._normalize_account(extraction.account_number)
        document_addresses = [
            self._normalize_address(extraction.service_address),
            self._normalize_address(extraction.billing_address),
            self._normalize_address(extraction.physical_billing_address),
        ]
        previous_payment_amount = extraction.previous_payment_amount
        previous_amount_due = extraction.previous_amount_due

        best: ClientMatch | None = None
        runner_up = 0
        for document in self.repository.list_confirmed_documents(limit=2000):
            historical = InvoiceExtraction.model_validate(document.get("extraction") or {})
            score = 0
            reasons: list[str] = []
            historical_vendor = normalize_vendor(historical.vendor or historical.payable_to)
            if vendor and historical_vendor and vendor == historical_vendor:
                score += 70
            historical_account = self._normalize_account(historical.account_number)
            if account_number and historical_account:
                if account_number == historical_account:
                    score += 260
                    reasons.append(f"vendor/account {historical.account_number}")
                elif len(account_number) >= 6 and (account_number in historical_account or historical_account in account_number):
                    score += 220
                    reasons.append(f"vendor/account {historical.account_number}")
            address_score, address_reason = self._best_historical_address_score(document_addresses, historical)
            score += address_score
            if address_reason:
                reasons.append(address_reason)
            if previous_payment_amount is not None or previous_amount_due is not None:
                historical_amounts = [historical.previous_payment_amount, historical.previous_amount_due, historical.amount_due, historical.total]
                if any(
                    reference is not None and value is not None and abs(reference - value) <= 0.01
                    for reference in historical_amounts
                    for value in (previous_payment_amount, previous_amount_due)
                ):
                    score += 25
                    reasons.append("prior payment amount")
            if score <= 0:
                continue

            client_name = self.client_lookup.client_name_for_po_box(document["po_box"]) or document["po_box"]
            candidate = ClientMatch(
                po_box=document["po_box"],
                client_id=0,
                client_name=client_name,
                matched_alias=", ".join(dict.fromkeys(reasons)) or "history",
                score=score,
            )
            if best is None or candidate.score > best.score:
                if best is not None:
                    runner_up = max(runner_up, best.score)
                best = candidate
            else:
                runner_up = max(runner_up, candidate.score)

        if best is None:
            return None
        if best.score < 180:
            return None
        if best.score < 280 and best.score - runner_up < 35:
            return None
        return best

    def _best_historical_address_score(self, document_addresses: list[str], historical: InvoiceExtraction) -> tuple[int, str]:
        history_addresses = [
            ("service address", self._normalize_address(historical.service_address)),
            ("billing address", self._normalize_address(historical.billing_address)),
            ("billing address", self._normalize_address(historical.physical_billing_address)),
        ]
        best_score = 0
        best_reason = ""
        for left in document_addresses:
            if not self._useful_address(left):
                continue
            for label, right in history_addresses:
                if not self._useful_address(right):
                    continue
                if left == right:
                    return 180, label
                if left in right or right in left:
                    best_score = max(best_score, 150)
                    best_reason = label
                    continue
                if SequenceMatcher(None, left, right).ratio() >= 0.88:
                    best_score = max(best_score, 140)
                    best_reason = label
        return best_score, best_reason

    def _normalize_account(self, value: str | None) -> str:
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())

    def _normalize_address(self, value: str | None) -> str:
        return normalize_text(value)

    def _useful_address(self, value: str) -> bool:
        return bool(value and len(value) >= 10 and any(ch.isdigit() for ch in value))
