from pathlib import Path

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.documents import archive_document_path, resolve_document_file_path, retain_document_copy


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        watch_root=tmp_path / "watch",
        database_path=tmp_path / "storage" / "test.db",
        poll_seconds=1,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(enabled=False),
    )


def test_retain_document_copy_creates_stable_archive(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    source_path = tmp_path / "watch" / "1001" / "review" / "inv_abc.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"%PDF-1.4 sample")

    archived_path = retain_document_copy(settings, "1001", "inv_abc", source_path)

    assert archived_path == archive_document_path(settings, "1001", "inv_abc", ".pdf")
    assert archived_path.exists()
    assert archived_path.read_bytes() == source_path.read_bytes()


def test_resolve_document_file_path_falls_back_to_archived_copy(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    archived_path = archive_document_path(settings, "1001", "inv_abc", ".pdf")
    archived_path.parent.mkdir(parents=True, exist_ok=True)
    archived_path.write_bytes(b"%PDF-1.4 archived")

    document = {
        "id": "inv_abc",
        "po_box": "1001",
        "original_filename": "sample.pdf",
        "current_file_path": str(tmp_path / "watch" / "1001" / "review" / "inv_abc.pdf"),
        "alignment": {
            "archived_file_path": str(archived_path),
        },
    }

    resolved = resolve_document_file_path(settings, document)

    assert resolved == archived_path
