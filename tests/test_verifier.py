from pathlib import Path

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.schemas import InvoiceExtraction
from app.verifier import Verifier


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        watch_root=tmp_path,
        database_path=tmp_path / "test.db",
        poll_seconds=1,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(verified_confidence=0.9, flagged_amount=50000, total_delta_tolerance=1.0),
        rails=RailsSettings(enabled=False),
    )


def test_verifier_flags_total_mismatch(tmp_path: Path) -> None:
    verifier = Verifier(build_settings(tmp_path))
    extraction = InvoiceExtraction(
        vendor="Acme",
        invoice_number="INV-1",
        invoice_date="2026-03-08",
        subtotal=100.0,
        tax=10.0,
        total=150.0,
        currency="USD",
        payment_terms="Net 30",
        po_box="1001",
        confidence=0.95,
    )
    result = verifier.verify(extraction, {})
    assert result.status == "review"
    assert any(issue.field == "total" for issue in result.issues)
