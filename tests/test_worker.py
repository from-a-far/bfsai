from pathlib import Path

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.intake import ScanIntakeService
from app.repository import Repository
from app.worker import iter_scan_roots
from app.schemas import OcrResult
from app.documents import po_box_layout
from app.worker import sweep_scans_once


def test_iter_scan_roots_includes_legacy_client_drop_locations(tmp_path: Path) -> None:
    watch_root = tmp_path / "clients"
    scan_root = tmp_path / "server" / "scans"

    roots = iter_scan_roots(watch_root, scan_root)

    assert roots == [scan_root, watch_root / "scans", watch_root]


def test_iter_scan_roots_deduplicates_equivalent_paths(tmp_path: Path) -> None:
    watch_root = tmp_path / "clients"
    scan_root = watch_root / "scans"

    roots = iter_scan_roots(watch_root, scan_root)

    assert roots == [scan_root, watch_root]


def test_sweep_scans_once_ignores_hidden_files(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        watch_root=tmp_path / "watch",
        database_path=tmp_path / "storage" / "test.db",
        poll_seconds=1,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(enabled=False),
        scan_root=tmp_path / "server" / "scans",
    )
    repository = Repository(settings.database_path)
    intake = ScanIntakeService(settings, repository)
    po_box_layout(settings, "5010234")
    settings.scan_root.mkdir(parents=True, exist_ok=True)
    (settings.scan_root / ".DS_Store").write_text("ignore me", encoding="utf-8")
    visible = settings.scan_root / "bill.pdf"
    visible.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        intake.extractor,
        "read_text",
        lambda _: OcrResult(
            text="Utility invoice for client PO Box 5010234 Amount Due $120.00",
            page_count=1,
            pages=[],
        ),
    )

    count = sweep_scans_once(intake, settings.scan_root)

    assert count == 1
    assert (settings.scan_root / ".DS_Store").exists()
    assert (settings.watch_root / "5010234" / "new" / "bill.pdf").exists()
