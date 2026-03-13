from pathlib import Path

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.extractor import Extractor


def test_heuristic_extract_uses_learned_field_candidates() -> None:
    settings = Settings(
        watch_root=Path("/tmp"),
        database_path=Path("/tmp/bfsai-test.db"),
        poll_seconds=3,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(),
    )
    extractor = Extractor(settings)

    extraction = extractor.extract(
        "1001",
        "Statement for service account",
        {
            "matched_vendor": "Acme Utility",
            "learned_field_candidates": {
                "account_number": {"value": "ACC-100"},
                "amount_due": {"value": 125.5},
                "due_date": {"value": "2026-03-31"},
                "billing_address": {"value": "123 Main St\nSuite 200"},
            },
        },
    )

    assert extraction.vendor == "Acme Utility"
    assert extraction.account_number == "ACC-100"
    assert extraction.amount_due == 125.5
    assert extraction.due_date == "2026-03-31"
    assert extraction.billing_address == "123 Main St\nSuite 200"
