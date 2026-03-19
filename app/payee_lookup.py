from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any

import httpx

from .client_lookup import ClientMatch
from .config import Settings
from .schemas import InvoiceExtraction
from .utils import as_float, normalize_text, normalize_vendor


LOGGER = logging.getLogger("bfsai.payee_lookup")


class PayeeAccountLookupService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._query_cache: dict[str, list[dict[str, Any]]] = {}

    def match_client(self, extraction: InvoiceExtraction) -> ClientMatch | None:
        search_terms = [term for term in (extraction.vendor, extraction.payable_to) if term and term.strip()]
        if not search_terms:
            return None

        best: ClientMatch | None = None
        runner_up = 0
        for term in search_terms:
            for record in self._search_payee_accounts(term):
                match = self._score_payee_record(record, extraction)
                if not match:
                    continue
                if best is None or match.score > best.score:
                    if best is not None:
                        runner_up = max(runner_up, best.score)
                    best = match
                else:
                    runner_up = max(runner_up, match.score)

        if best is None:
            return None
        if best.score < 180:
            return None
        if best.score < 280 and best.score - runner_up < 40:
            return None
        return best

    def _score_payee_record(self, record: dict[str, Any], extraction: InvoiceExtraction) -> ClientMatch | None:
        clients = [client for client in record.get("clients") or [] if str(client.get("pobox") or "").strip()]
        if not clients:
            return None

        vendor_text = " ".join(
            value for value in (record.get("alias"), record.get("payable_to"), record.get("holder"), record.get("attn")) if value
        )
        vendor_score = self._vendor_score(extraction, vendor_text)
        account_score = self._account_score(extraction.account_number, record.get("number"))
        address_score, address_reason = self._address_score(extraction, record)
        payment_score = self._payment_score(extraction, record.get("bill") or {})

        total_score = vendor_score + account_score + address_score + payment_score
        if account_score <= 0 and address_score <= 0:
            return None

        reasons = []
        if account_score > 0:
            reasons.append(f"payee account {record.get('number')}")
        if address_reason:
            reasons.append(address_reason)
        if payment_score > 0:
            reasons.append("prior payment amount")

        best_client: ClientMatch | None = None
        for client in clients:
            po_box = str(client.get("pobox") or "").strip()
            if not po_box:
                continue
            client_name = self._display_name(client) or po_box
            candidate = ClientMatch(
                po_box=po_box,
                client_id=int(client.get("id") or 0),
                client_name=client_name,
                matched_alias=", ".join(reasons) or "payee account",
                score=total_score,
            )
            if best_client is None or candidate.score > best_client.score:
                best_client = candidate
        return best_client

    def _vendor_score(self, extraction: InvoiceExtraction, vendor_text: str | None) -> int:
        search_terms = [term for term in (extraction.vendor, extraction.payable_to) if term and term.strip()]
        haystack = normalize_vendor(vendor_text)
        if not search_terms or not haystack:
            return 0
        best = 0
        for term in search_terms:
            needle = normalize_vendor(term)
            if not needle:
                continue
            if needle == haystack or needle in haystack or haystack in needle:
                best = max(best, 90)
                continue
            ratio = SequenceMatcher(None, needle, haystack).ratio()
            if ratio >= 0.82:
                best = max(best, 55)
        return best

    def _account_score(self, extracted_account: str | None, payee_account: str | None) -> int:
        left = self._normalize_account(extracted_account)
        right = self._normalize_account(payee_account)
        if not left or not right:
            return 0
        if left == right:
            return 260
        if len(left) >= 6 and (left in right or right in left):
            return 220
        if SequenceMatcher(None, left, right).ratio() >= 0.88:
            return 180
        return 0

    def _address_score(self, extraction: InvoiceExtraction, record: dict[str, Any]) -> tuple[int, str]:
        document_addresses = [
            ("service address", extraction.service_address),
            ("billing address", extraction.billing_address),
            ("billing address", extraction.physical_billing_address),
        ]
        payee_addresses = [
            record.get("service_address"),
            self._join_address(record.get("physical_address") or {}),
            self._join_address(record.get("individual_address") or {}),
            self._join_address((record.get("location") or {}).get("address") or {}),
        ]

        best_score = 0
        best_reason = ""
        for label, document_address in document_addresses:
            left = self._normalize_address(document_address)
            if not self._useful_address(left):
                continue
            for payee_address in payee_addresses:
                right = self._normalize_address(payee_address)
                if not self._useful_address(right):
                    continue
                if left == right:
                    return 185, label
                if left in right or right in left:
                    best_score = max(best_score, 155)
                    best_reason = label
                    continue
                ratio = SequenceMatcher(None, left, right).ratio()
                if ratio >= 0.88:
                    best_score = max(best_score, 145)
                    best_reason = label
        return best_score, best_reason

    def _payment_score(self, extraction: InvoiceExtraction, bill: dict[str, Any]) -> int:
        candidates = [
            extraction.previous_payment_amount,
            extraction.previous_amount_due,
        ]
        expected = as_float(bill.get("amount"))
        if expected is None:
            return 0
        for value in candidates:
            if value is None:
                continue
            if abs(value - expected) <= 0.01:
                return 35
        return 0

    def _search_payee_accounts(self, term: str) -> list[dict[str, Any]]:
        query = normalize_vendor(term)
        if not query:
            return []
        cached = self._query_cache.get(query)
        if cached is not None:
            return cached

        auth_name = self.settings.rails.auth_name.strip()
        auth_password = self.settings.rails.auth_password.strip()
        if not auth_name or not auth_password:
            return []

        base_url = self.settings.rails.base_url.rstrip("/")
        try:
            with httpx.Client(base_url=base_url, timeout=self.settings.rails.timeout_seconds, follow_redirects=True) as client:
                token = self._login(client, auth_name, auth_password)
                if not token:
                    return []
                response = client.get(
                    self.settings.rails.payee_accounts_path,
                    params={"search": term},
                    headers={"Accept": "application/json", "Authorization": f'Token token="{token}"'},
                )
                response.raise_for_status()
                payload = response.json()
                records = [record for record in payload.get("data") or [] if isinstance(record, dict)]
        except Exception as error:
            LOGGER.warning("payee account lookup failed for %r: %s", term, error)
            records = []

        self._query_cache[query] = records
        return records

    def _login(self, client: httpx.Client, auth_name: str, auth_password: str) -> str | None:
        response = client.post(
            self.settings.rails.login_path,
            data={"name": auth_name, "password": auth_password},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("token") or "").strip()
        return token or None

    def _display_name(self, record: dict[str, Any]) -> str:
        client_contact_name = " ".join(str(record.get("client_contact_name") or "").split())
        friendly_name = " ".join(str(record.get("friendly_name") or "").split())
        name = " ".join(str(record.get("name") or "").split())
        base = client_contact_name or name or friendly_name
        if friendly_name and normalize_vendor(friendly_name) not in normalize_vendor(base):
            return f"{base} ({friendly_name})"
        return base

    def _normalize_account(self, value: str | None) -> str:
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())

    def _normalize_address(self, value: str | None) -> str:
        return normalize_text(value)

    def _useful_address(self, value: str) -> bool:
        return bool(value and len(value) >= 10 and any(ch.isdigit() for ch in value))

    def _join_address(self, address: dict[str, Any]) -> str:
        if not isinstance(address, dict):
            return ""
        parts = [
            address.get("line_1"),
            address.get("line_2"),
            address.get("city"),
            address.get("state"),
            address.get("zip_1"),
            address.get("zip_2"),
        ]
        return " ".join(str(part).strip() for part in parts if part).strip()
