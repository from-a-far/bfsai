from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import httpx
import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from .config import Settings
from .schemas import InvoiceExtraction, InvoiceLineItem, OcrPage, OcrResult, OcrWord
from .utils import as_float, compact_excerpt, normalize_text


DATE_PATTERNS = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"),
]

ADDRESS_LABELS = {
    "remittance_address": ["remittance address", "remit to", "remit payment to", "mail payment to"],
    "billing_address": ["billing address", "bill to"],
    "physical_billing_address": ["physical billing address", "billing location"],
    "service_address": ["service address", "service location", "premise address"],
}

TEXT_LABELS = {
    "payable_to": ["payable to", "make checks payable to", "pay to"],
    "account_number": ["account number", "acct #", "account #", "account no", "account"],
    "friendly_name": ["friendly name", "nickname", "location name"],
    "name_on_account": ["name on account", "account name"],
}

AMOUNT_LABELS = {
    "subtotal": ["subtotal", "sub total", "amount before tax"],
    "tax": ["tax", "vat", "sales tax"],
    "total": ["total", "invoice total", "total amount"],
    "amount_due": ["amount due", "balance due", "current amount due", "payment due"],
    "previous_amount_due": ["previous amount due", "previous balance", "prior amount due"],
    "previous_payment_amount": ["previous payment amount", "payment received", "last payment", "previous payment"],
}

DATE_LABELS = {
    "invoice_date": ["invoice date", "statement date", "bill date", "date"],
    "due_date": ["due date", "payment due date"],
    "previous_payment_date": ["previous payment date", "last payment date", "payment received date"],
}


class Extractor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.has_tesseract = shutil.which("tesseract") is not None

    def read_text(self, file_path: Path) -> OcrResult:
        if file_path.suffix.lower() == ".pdf":
            return self._read_pdf(file_path)
        return self._read_image(file_path)

    def extract(self, po_box: str, text: str, hints: dict[str, Any]) -> InvoiceExtraction:
        if self.settings.ollama.enabled:
            llm_result = self._extract_with_ollama(po_box, text, hints)
            if llm_result:
                return llm_result
        return self._heuristic_extract(po_box, text, hints)

    def _read_pdf(self, file_path: Path) -> OcrResult:
        document = pdfium.PdfDocument(str(file_path))
        pages: list[OcrPage] = []
        for page_number in range(len(document)):
            page = document[page_number]
            textpage = page.get_textpage()
            native_text = textpage.get_text_range().strip()
            if native_text:
                width = int(page.get_width())
                height = int(page.get_height())
                words = []
                if self.has_tesseract:
                    bitmap = page.render(scale=2).to_pil()
                    ocr_page = self._ocr_image(bitmap, page_number + 1)
                    width = ocr_page.width
                    height = ocr_page.height
                    words = ocr_page.words
                pages.append(
                    OcrPage(
                        page_number=page_number + 1,
                        width=width,
                        height=height,
                        text=native_text,
                        words=words,
                    )
                )
                continue
            if not self.has_tesseract:
                raise RuntimeError("Tesseract is not installed and this PDF does not contain selectable text")
            bitmap = page.render(scale=2).to_pil()
            pages.append(self._ocr_image(bitmap, page_number + 1))
        return OcrResult(
            text="\n\n".join(page.text for page in pages),
            page_count=len(pages),
            pages=pages,
        )

    def _read_image(self, file_path: Path) -> OcrResult:
        if not self.has_tesseract:
            raise RuntimeError("Tesseract is not installed and is required for image OCR")
        with Image.open(file_path) as image:
            page = self._ocr_image(image.convert("RGB"), 1)
        return OcrResult(text=page.text, page_count=1, pages=[page])

    def _ocr_image(self, image: Image.Image, page_number: int) -> OcrPage:
        text = pytesseract.image_to_string(image)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        words: list[OcrWord] = []
        for index, token in enumerate(data.get("text", [])):
            token = token.strip()
            normalized = normalize_text(token)
            if not normalized:
                continue
            try:
                confidence = float(data["conf"][index])
            except (KeyError, TypeError, ValueError):
                confidence = 0.0
            words.append(
                OcrWord(
                    text=token,
                    normalized_text=normalized,
                    page_number=page_number,
                    left=int(data["left"][index]),
                    top=int(data["top"][index]),
                    width=int(data["width"][index]),
                    height=int(data["height"][index]),
                    confidence=confidence,
                )
            )
        return OcrPage(
            page_number=page_number,
            width=image.width,
            height=image.height,
            text=text,
            words=words,
        )

    def _extract_with_ollama(
        self,
        po_box: str,
        text: str,
        hints: dict[str, Any],
    ) -> InvoiceExtraction | None:
        prompt = (
            "Extract invoice data as JSON with keys "
            "vendor, payable_to, remittance_address, billing_address, physical_billing_address, "
            "service_address, account_number, friendly_name, name_on_account, invoice_number, "
            "invoice_date, due_date, subtotal, tax, total, amount_due, previous_amount_due, "
            "previous_payment_date, previous_payment_amount, currency, payment_terms, po_box, "
            "line_items, confidence. "
            "line_items must be an array of objects with description, quantity, unit_price, amount. "
            "Prefer exact values from the document. Use null when missing. "
            f"PO box: {po_box}. Learning hints: {json.dumps(hints)}. "
            f"Invoice text:\n{text[:12000]}"
        )
        try:
            response = httpx.post(
                f"{self.settings.ollama.base_url}/api/generate",
                json={
                    "model": self.settings.ollama.model,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                },
                timeout=self.settings.ollama.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            parsed = json.loads(payload.get("response", "{}"))
            return InvoiceExtraction(
                vendor=parsed.get("vendor"),
                payable_to=parsed.get("payable_to"),
                remittance_address=parsed.get("remittance_address"),
                billing_address=parsed.get("billing_address"),
                physical_billing_address=parsed.get("physical_billing_address"),
                service_address=parsed.get("service_address"),
                account_number=parsed.get("account_number"),
                friendly_name=parsed.get("friendly_name"),
                name_on_account=parsed.get("name_on_account"),
                invoice_number=parsed.get("invoice_number"),
                invoice_date=parsed.get("invoice_date"),
                due_date=parsed.get("due_date"),
                subtotal=as_float(parsed.get("subtotal")),
                tax=as_float(parsed.get("tax")),
                total=as_float(parsed.get("total")),
                amount_due=as_float(parsed.get("amount_due")),
                previous_amount_due=as_float(parsed.get("previous_amount_due")),
                previous_payment_date=parsed.get("previous_payment_date"),
                previous_payment_amount=as_float(parsed.get("previous_payment_amount")),
                currency=(parsed.get("currency") or "USD"),
                payment_terms=parsed.get("payment_terms"),
                po_box=po_box,
                line_items=[
                    InvoiceLineItem(
                        description=str(item.get("description", "")),
                        quantity=as_float(item.get("quantity")),
                        unit_price=as_float(item.get("unit_price")),
                        amount=as_float(item.get("amount")),
                    )
                    for item in parsed.get("line_items", [])
                    if item.get("description")
                ],
                confidence=float(parsed.get("confidence") or 0.75),
                raw_text_excerpt=compact_excerpt(text),
                model_source=f"ollama:{self.settings.ollama.model}",
                learning_hints=hints,
            )
        except Exception:
            return None

    def _heuristic_extract(
        self,
        po_box: str,
        text: str,
        hints: dict[str, Any],
    ) -> InvoiceExtraction:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        vendor = next((line for line in lines[:6] if len(line) > 3 and not any(ch.isdigit() for ch in line[:4])), None)
        hinted_vendor = self._hinted_text(hints, "vendor") or hints.get("matched_vendor")
        payable_to = self._label_text(text, TEXT_LABELS["payable_to"])
        if payable_to:
            vendor = payable_to
        else:
            vendor = hinted_vendor or vendor
        payable_to = payable_to or self._hinted_text(hints, "payable_to")
        account_number = self._label_text(text, TEXT_LABELS["account_number"])
        account_number = account_number or self._hinted_text(hints, "account_number")
        friendly_name = self._label_text(text, TEXT_LABELS["friendly_name"]) or self._hinted_text(hints, "friendly_name")
        name_on_account = self._label_text(text, TEXT_LABELS["name_on_account"]) or self._hinted_text(hints, "name_on_account")
        invoice_number = self._match_first(
            text,
            [
                r"invoice\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9\-\/]+)",
                r"inv\s*#\s*([A-Z0-9\-\/]+)",
            ],
        )
        invoice_number = invoice_number or self._hinted_text(hints, "invoice_number")
        invoice_date = self._label_date(text, DATE_LABELS["invoice_date"]) or self._extract_date(text) or self._hinted_text(hints, "invoice_date")
        due_date = self._label_date(text, DATE_LABELS["due_date"]) or self._hinted_text(hints, "due_date")
        subtotal = self._label_amount(text, AMOUNT_LABELS["subtotal"])
        tax = self._label_amount(text, AMOUNT_LABELS["tax"])
        total = self._label_amount(text, AMOUNT_LABELS["total"])
        amount_due = self._label_amount(text, AMOUNT_LABELS["amount_due"])
        previous_amount_due = self._label_amount(text, AMOUNT_LABELS["previous_amount_due"])
        previous_payment_date = self._label_date(text, DATE_LABELS["previous_payment_date"]) or self._hinted_text(hints, "previous_payment_date")
        previous_payment_amount = self._label_amount(text, AMOUNT_LABELS["previous_payment_amount"])
        subtotal = subtotal if subtotal is not None else self._hinted_amount(hints, "subtotal")
        tax = tax if tax is not None else self._hinted_amount(hints, "tax")
        total = total if total is not None else self._hinted_amount(hints, "total")
        amount_due = amount_due if amount_due is not None else self._hinted_amount(hints, "amount_due")
        previous_amount_due = previous_amount_due if previous_amount_due is not None else self._hinted_amount(hints, "previous_amount_due")
        previous_payment_amount = (
            previous_payment_amount
            if previous_payment_amount is not None
            else self._hinted_amount(hints, "previous_payment_amount")
        )
        if total is None:
            numbers = [as_float(match) for match in re.findall(r"\$?\d[\d,]*\.\d{2}", text)]
            totals = [number for number in numbers if number is not None]
            total = max(totals) if totals else None
        if amount_due is None:
            amount_due = total

        currency = "USD"
        if "eur" in text.lower() or "€" in text:
            currency = "EUR"
        elif "gbp" in text.lower() or "£" in text:
            currency = "GBP"
        currency = self._hinted_text(hints, "currency") or currency

        payment_terms = self._match_first(text, [r"(net\s+\d+)", r"(due on receipt)"])
        if not payment_terms:
            payment_terms = self._hinted_text(hints, "payment_terms")

        remittance_address = self._label_address_block(lines, ADDRESS_LABELS["remittance_address"]) or self._hinted_text(hints, "remittance_address")
        billing_address = self._label_address_block(lines, ADDRESS_LABELS["billing_address"]) or self._hinted_text(hints, "billing_address")
        physical_billing_address = (
            self._label_address_block(lines, ADDRESS_LABELS["physical_billing_address"])
            or self._hinted_text(hints, "physical_billing_address")
        )
        service_address = self._label_address_block(lines, ADDRESS_LABELS["service_address"]) or self._hinted_text(hints, "service_address")

        line_items = self._extract_line_items(lines)

        signals = [
            vendor,
            payable_to,
            invoice_number,
            invoice_date,
            due_date,
            subtotal,
            tax,
            total,
            amount_due,
        ]
        confidence = round(sum(1 for signal in signals if signal not in (None, "")) / len(signals), 2)
        if hints:
            confidence = min(0.97, confidence + 0.08)

        return InvoiceExtraction(
            vendor=vendor,
            payable_to=payable_to,
            remittance_address=remittance_address,
            billing_address=billing_address,
            physical_billing_address=physical_billing_address,
            service_address=service_address,
            account_number=account_number,
            friendly_name=friendly_name,
            name_on_account=name_on_account,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            subtotal=subtotal,
            tax=tax,
            total=total,
            amount_due=amount_due,
            previous_amount_due=previous_amount_due,
            previous_payment_date=previous_payment_date,
            previous_payment_amount=previous_payment_amount,
            currency=currency,
            payment_terms=payment_terms,
            po_box=po_box,
            line_items=line_items,
            confidence=confidence,
            raw_text_excerpt=compact_excerpt(text),
            model_source="heuristic",
            learning_hints=hints,
        )

    def _hinted_text(self, hints: dict[str, Any], field_name: str) -> str | None:
        learned = (hints.get("learned_field_candidates") or {}).get(field_name)
        if isinstance(learned, dict):
            value = learned.get("value")
            if value not in (None, "", []):
                return str(value)
        confirmed = (hints.get("confirmed_fields") or {}).get(field_name)
        if confirmed in (None, "", []):
            return None
        return str(confirmed)

    def _hinted_amount(self, hints: dict[str, Any], field_name: str) -> float | None:
        learned = (hints.get("learned_field_candidates") or {}).get(field_name)
        if isinstance(learned, dict):
            learned_value = as_float(learned.get("value"))
            if learned_value is not None:
                return learned_value
        return as_float((hints.get("confirmed_fields") or {}).get(field_name))

    def _label_amount(self, text: str, labels: list[str]) -> float | None:
        for label in labels:
            match = re.search(rf"{label}\s*[:\-]?\s*\$?([\d,]+\.\d{{2}})", text, flags=re.IGNORECASE)
            if match:
                return as_float(match.group(1))
        return None

    def _label_text(self, text: str, labels: list[str]) -> str | None:
        for label in labels:
            match = re.search(rf"{label}\s*[:\-]?\s*(.+)", text, flags=re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).splitlines()[0].strip()
            value = re.split(r"\s{2,}", value)[0].strip(" -:")
            if value:
                return value
        return None

    def _label_date(self, text: str, labels: list[str]) -> str | None:
        for label in labels:
            match = re.search(
                rf"{label}\s*[:\-]?\s*(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}})",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return None

    def _label_address_block(self, lines: list[str], labels: list[str]) -> str | None:
        normalized_lines = [line.lower() for line in lines]
        for index, line in enumerate(normalized_lines):
            if not any(label in line for label in labels):
                continue
            block: list[str] = []
            same_line = re.split(r":", lines[index], maxsplit=1)
            if len(same_line) == 2 and same_line[1].strip():
                block.append(same_line[1].strip())
            for candidate in lines[index + 1 : index + 5]:
                if not candidate or ":" in candidate and len(block) >= 1:
                    break
                block.append(candidate)
                if len(block) >= 3:
                    break
            value = ", ".join(part.strip(" ,") for part in block if part.strip(" ,"))
            if value:
                return value
        return None

    def _extract_date(self, text: str) -> str | None:
        for pattern in DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        return None

    def _extract_line_items(self, lines: list[str]) -> list[InvoiceLineItem]:
        items: list[InvoiceLineItem] = []
        for line in lines:
            amount_match = re.search(r"(.+?)\s+\$?([\d,]+\.\d{2})$", line)
            if not amount_match:
                continue
            description = amount_match.group(1).strip(" -:")
            if len(description) < 3:
                continue
            amount = as_float(amount_match.group(2))
            if amount is None:
                continue
            items.append(InvoiceLineItem(description=description, amount=amount))
            if len(items) >= 10:
                break
        return items

    def _match_first(self, text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None
