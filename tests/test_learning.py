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
            "alignment": {
                "page_count": 1,
                "field_alignments": {
                    "vendor": {
                        "page_number": 1,
                        "normalized_bbox": {
                            "left": 0.1,
                            "top": 0.1,
                            "width": 0.2,
                            "height": 0.05,
                        },
                    },
                    "invoice_number": {
                        "page_number": 1,
                        "normalized_bbox": {
                            "left": 0.4,
                            "top": 0.1,
                            "width": 0.12,
                            "height": 0.04,
                        },
                    },
                },
            },
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
    assert {profile["field_name"] for profile in hints["field_alignment_profiles"]} == {"vendor", "invoice_number"}
