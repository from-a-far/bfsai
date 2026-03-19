from pathlib import Path

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.payee_lookup import PayeeAccountLookupService
from app.schemas import InvoiceExtraction


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        watch_root=tmp_path / "watch",
        database_path=tmp_path / "storage" / "test.db",
        poll_seconds=1,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(enabled=True),
        scan_root=tmp_path / "server" / "scans",
    )


def test_match_client_prefers_vendor_account_and_service_address(tmp_path: Path) -> None:
    service = PayeeAccountLookupService(build_settings(tmp_path))
    service._search_payee_accounts = lambda _: [  # type: ignore[method-assign]
        {
            "alias": "Eversource",
            "number": "1421 503 0033",
            "service_address": "577 Sea View Avenue Osterville MA 02655",
            "bill": {"amount": 221.45},
            "clients": [
                {
                    "id": 99,
                    "pobox": 5010095,
                    "name": "Susanne L White",
                    "friendly_name": "",
                    "client_contact_name": "White, Susanne",
                }
            ],
        }
    ]

    match = service.match_client(
        InvoiceExtraction(
            po_box="unresolved",
            vendor="Eversource",
            account_number="14215030033",
            service_address="577 Sea View Avenue Osterville MA 02655",
            previous_payment_amount=221.45,
        )
    )

    assert match is not None
    assert match.po_box == "5010095"
    assert match.score >= 300
