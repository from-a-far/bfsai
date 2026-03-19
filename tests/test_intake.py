from pathlib import Path

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.documents import po_box_layout
from app.intake import ScanIntakeService
from app.repository import Repository
from app.schemas import InvoiceExtraction, OcrResult


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        watch_root=tmp_path / "watch",
        database_path=tmp_path / "storage" / "test.db",
        poll_seconds=1,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(enabled=False),
        scan_root=tmp_path / "server" / "scans",
    )


def build_service(tmp_path: Path) -> ScanIntakeService:
    settings = build_settings(tmp_path)
    repository = Repository(settings.database_path)
    return ScanIntakeService(settings, repository)


def test_bill_scan_routes_into_client_new(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    po_box_layout(service.settings, "5010234")
    source_path = service.settings.scan_root / "bill.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Utility invoice for client PO Box 5010234 Amount Due $120.00",
            page_count=1,
            pages=[],
        ),
    )

    destination = service.process_scan(source_path)

    assert destination == service.settings.watch_root / "5010234" / "new" / "bill.pdf"
    assert destination.exists()
    assert not source_path.exists()


def test_credit_card_scan_routes_into_new_and_copies_to_type_folder(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    po_box_layout(service.settings, "5010234")
    source_path = service.settings.scan_root / "statement.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Cardmember statement available credit minimum payment due PO Box 5010234",
            page_count=1,
            pages=[],
        ),
    )

    destination = service.process_scan(source_path)

    assert destination == service.settings.watch_root / "5010234" / "new" / "statement.pdf"
    assert destination.exists()
    assert (service.settings.watch_root / "5010234" / "credit_card_statements" / "statement.pdf").exists()


def test_non_bill_statement_moves_to_type_folder(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    po_box_layout(service.settings, "5010234")
    source_path = service.settings.scan_root / "bank.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Statement period 01/01/2026 to 01/31/2026 ending balance PO Box 5010234",
            page_count=1,
            pages=[],
        ),
    )

    destination = service.process_scan(source_path)

    assert destination == service.settings.watch_root / "5010234" / "statments" / "bank.pdf"
    assert destination.exists()
    assert not (service.settings.watch_root / "5010234" / "new" / "bank.pdf").exists()


def test_unresolved_bill_scan_routes_into_unresolved_new(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    source_path = service.settings.scan_root / "legacy.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Vendor invoice Amount Due $120.00 no mailbox reference",
            page_count=1,
            pages=[],
        ),
    )

    destination = service.process_scan(source_path)

    assert destination == service.settings.watch_root / "unresolved" / "new" / "legacy.pdf"
    assert destination.exists()


def test_bill_scan_routes_by_matched_client_name_when_po_box_missing(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    po_box_layout(service.settings, "5010234")
    source_path = service.settings.scan_root / "named.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Invoice for Mr. & Mrs. White Amount Due $120.00",
            page_count=1,
            pages=[],
        ),
    )
    monkeypatch.setattr(
        service.client_lookup,
        "match_po_box",
        lambda _: type("Match", (), {"po_box": "5010234"})(),
    )

    destination = service.process_scan(source_path)

    assert destination == service.settings.watch_root / "5010234" / "new" / "named.pdf"
    assert destination.exists()


def test_bill_scan_uses_forced_client_override(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    po_box_layout(service.settings, "5010234")
    source_path = service.settings.scan_root / "forced.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Invoice for unknown client Amount Due $120.00",
            page_count=1,
            pages=[],
        ),
    )

    destination = service.process_scan(source_path, forced_client_key="5010234")

    assert destination == service.settings.watch_root / "5010234" / "new" / "forced.pdf"
    assert destination.exists()


def test_bill_scan_routes_by_payee_account_match(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    po_box_layout(service.settings, "5010234")
    source_path = service.settings.scan_root / "payee.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Utility bill amount due $120.00",
            page_count=1,
            pages=[],
        ),
    )
    monkeypatch.setattr(
        service.client_lookup,
        "match_po_box",
        lambda _: None,
    )
    monkeypatch.setattr(
        service.extractor,
        "extract",
        lambda _: InvoiceExtraction(po_box="unresolved", vendor="Eversource", account_number="14215030033"),
    )
    monkeypatch.setattr(
        service.payee_lookup,
        "match_client",
        lambda _: type("Match", (), {"po_box": "5010234"})(),
    )

    destination = service.process_scan(source_path)

    assert destination == service.settings.watch_root / "5010234" / "new" / "payee.pdf"
    assert destination.exists()


def test_bill_scan_routes_by_historical_vendor_account_and_address(monkeypatch, tmp_path: Path) -> None:
    service = build_service(tmp_path)
    po_box_layout(service.settings, "5010234")
    historical_path = tmp_path / "storage" / "document_files" / "history.pdf"
    historical_path.parent.mkdir(parents=True, exist_ok=True)
    historical_path.write_bytes(b"%PDF-1.4 history")
    service.repository.upsert_document(
        {
            "id": "inv_history",
            "po_box": "5010234",
            "original_filename": "history.pdf",
            "current_file_path": str(historical_path),
            "current_json_path": "",
            "status": "approved",
            "vendor": "Eversource",
            "extraction": InvoiceExtraction(
                po_box="5010234",
                vendor="Eversource",
                account_number="1421 503 0033",
                service_address="577 SEA VIEW AVENUE OSTERVILLE MA 02655",
                previous_payment_amount=221.45,
            ).model_dump(),
            "verification": {},
            "alignment": {},
            "confirmed_at": "2026-03-18T00:00:00+00:00",
        }
    )

    source_path = service.settings.scan_root / "history-match.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        service.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Utility bill amount due $120.00",
            page_count=1,
            pages=[],
        ),
    )
    monkeypatch.setattr(
        service.client_lookup,
        "match_po_box",
        lambda _: None,
    )
    monkeypatch.setattr(
        service.payee_lookup,
        "match_client",
        lambda _: None,
    )
    monkeypatch.setattr(
        service.extractor,
        "extract",
        lambda _: InvoiceExtraction(
            po_box="unresolved",
            vendor="Eversource",
            account_number="14215030033",
            service_address="577 Sea View Avenue Osterville MA 02655",
            previous_payment_amount=221.45,
        ),
    )

    destination = service.process_scan(source_path)

    assert destination == service.settings.watch_root / "5010234" / "new" / "history-match.pdf"
    assert destination.exists()
