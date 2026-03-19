from pathlib import Path

from app.client_lookup import ClientLookupService
from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings


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


def test_match_po_box_uses_client_friendly_name(tmp_path: Path) -> None:
    service = ClientLookupService(build_settings(tmp_path))
    service._load_client_index = lambda: [  # type: ignore[method-assign]
        {
            "id": 7,
            "pobox": 5010095,
            "name": "White Family Trust",
            "friendly_name": "White Family",
            "client_contact_name": "White, John",
            "contacts": [],
        }
    ]

    match = service.match_po_box("Bill To White Family monthly services total due $475.00")

    assert match is not None
    assert match.po_box == "5010095"
    assert match.matched_alias == "White Family"


def test_match_po_box_uses_contact_name(tmp_path: Path) -> None:
    service = ClientLookupService(build_settings(tmp_path))
    service._load_client_index = lambda: [  # type: ignore[method-assign]
        {
            "id": 8,
            "pobox": 5010123,
            "name": "Client 8",
            "friendly_name": "",
            "client_contact_name": "",
            "contacts": [
                {
                    "friendly_name": "Mr. & Mrs. White",
                    "display_name": "John White",
                    "first_name": "John",
                    "last_name": "White",
                }
            ],
        }
    ]

    match = service.match_po_box("Ship To Mr. & Mrs. White service address total due $475.00")

    assert match is not None
    assert match.po_box == "5010123"
    assert match.matched_alias == "Mr. & Mrs. White"


def test_match_po_box_handles_nickname_and_close_spelling(tmp_path: Path) -> None:
    service = ClientLookupService(build_settings(tmp_path))
    service._load_client_index = lambda: [  # type: ignore[method-assign]
        {
            "id": 99,
            "pobox": 5010095,
            "name": "Susanne L White",
            "friendly_name": "",
            "client_contact_name": "White, Susanne",
            "contacts": [
                {"first_name": "Michael", "last_name": "White", "friendly_name": "", "display_name": "Michael White"},
                {"first_name": "Susanne", "last_name": "White", "friendly_name": "", "display_name": "Susanne White"},
            ],
        }
    ]

    susan_match = service.match_po_box("Billing Address Susan White P.O. BOX 5010 Monroe CT")
    mike_match = service.match_po_box("Statement of Account RE: MIKE WHITE P.O. BOX 5010 Monroe CT")

    assert susan_match is not None
    assert susan_match.po_box == "5010095"
    assert mike_match is not None
    assert mike_match.po_box == "5010095"


def test_match_po_box_reorders_last_first_contact_aliases(tmp_path: Path) -> None:
    service = ClientLookupService(build_settings(tmp_path))
    service._load_client_index = lambda: [  # type: ignore[method-assign]
        {
            "id": 99,
            "pobox": 5010095,
            "name": "",
            "friendly_name": "",
            "client_contact_name": "White, Susanne",
            "contacts": [],
        }
    ]

    match = service.match_po_box("Billing Address Susan White P.O. BOX 5010 Monroe CT")

    assert match is not None
    assert match.po_box == "5010095"


def test_list_clients_formats_display_label_for_people(tmp_path: Path) -> None:
    service = ClientLookupService(build_settings(tmp_path))
    service._load_client_index = lambda: [  # type: ignore[method-assign]
        {
            "id": 25,
            "pobox": 5010028,
            "name": "Martin  Schanback",
            "friendly_name": "Martin",
            "client_contact_name": "Schanback, Martin (Martin)",
            "contacts": [],
        }
    ]

    clients = service.list_clients()

    assert len(clients) == 1
    assert clients[0]["po_box"] == "5010028"
    assert clients[0]["label"] == "Schanback, Martin (Martin) - 5010028"
    assert clients[0]["display_name"] == "Schanback, Martin (Martin)"
    assert clients[0]["search_text"]
    assert "Martin  Schanback" in clients[0]["search_text"]
    assert "Schanback, Martin (Martin)" in clients[0]["search_text"]
