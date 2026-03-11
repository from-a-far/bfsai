from pathlib import Path

import httpx

from app.config import OllamaSettings, RailsSettings, Settings, ThresholdSettings
from app.ingestion import RailsIngestionService
from app.repository import Repository


def build_settings(tmp_path: Path, enabled: bool = True) -> Settings:
    return Settings(
        watch_root=tmp_path,
        database_path=tmp_path / "test.db",
        poll_seconds=1,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(enabled=enabled, base_url="http://rails.test", endpoint_path="/ingest", timeout_seconds=5),
    )


def test_ingestion_records_success(tmp_path: Path, monkeypatch) -> None:
    repository = Repository(tmp_path / "repo.db")
    repository.upsert_document(
        {
            "id": "inv_1234567890123456",
            "po_box": "1001",
            "original_filename": "sample.pdf",
            "current_file_path": "/tmp/sample.pdf",
            "current_json_path": "/tmp/sample.json",
            "status": "approved",
            "confidence": 1.0,
            "extraction": {"vendor": "Acme"},
            "verification": {},
            "alignment": {},
        }
    )

    class FakeResponse:
        status_code = 201
        text = '{"ok":true}'

        def raise_for_status(self) -> None:
            return None

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    service = RailsIngestionService(build_settings(tmp_path), repository)
    result = service.ingest_document(repository.get_document("inv_1234567890123456"))
    updated = repository.get_document("inv_1234567890123456")
    assert result["status"] == "ingested"
    assert updated["ingestion_status"] == "ingested"
    assert updated["ingestion_attempts"] == 1
