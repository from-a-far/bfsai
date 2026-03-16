from pathlib import Path

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.documents import po_box_layout
from app.intake import ScanIntakeService
from app.repository import Repository
from app.schemas import OcrResult


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
