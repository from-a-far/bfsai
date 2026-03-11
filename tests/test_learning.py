from pathlib import Path

from app.learning import LearningService
from app.repository import Repository


def test_learning_records_confirmed_vendor_profile(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "learning.db")
    service = LearningService(repository)
    repository.upsert_document(
        {
            "id": "inv_1234567890123456",
            "po_box": "1001",
            "original_filename": "sample.pdf",
            "current_file_path": "/tmp/sample.pdf",
            "current_json_path": "/tmp/sample.json",
            "status": "review",
            "confidence": 0.5,
            "extraction": {"vendor": "Acme Incorporated", "currency": "USD"},
            "verification": {},
            "alignment": {},
        }
    )
    document = repository.get_document("inv_1234567890123456")
    corrections = service.record_confirmation(
        document,
        {
            "vendor": "Acme Incorporated",
            "invoice_number": "INV-42",
            "invoice_date": "2026-03-08",
            "subtotal": 10.0,
            "tax": 1.0,
            "total": 11.0,
            "currency": "USD",
            "payment_terms": "Net 30",
        },
    )
    assert corrections == 6
    hints = service.build_hints("1001", "Invoice from ACME INCORPORATED dated 2026-03-08")
    assert hints["matched_vendor"] == "Acme Incorporated"
    assert hints["confirmed_fields"]["payment_terms"] == "Net 30"
