from pathlib import Path

from app.config import ExtractionSettings, OllamaSettings, RailsSettings, Settings, ThresholdSettings, _default_extraction_settings
from app.repository import Repository
from app.strategy import StrategyService
from app.training import TrainingService


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        watch_root=tmp_path / "watch",
        database_path=tmp_path / "storage" / "bfsai.db",
        poll_seconds=3,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(enabled=False),
        extraction=ExtractionSettings(
            active_strategy="legacy_local",
            corpus_dir=tmp_path / "storage" / "training_corpus",
            runs_dir=tmp_path / "storage" / "training_runs",
            runtime_dir=tmp_path / "storage" / "runtime",
            strategies=_default_extraction_settings().strategies,
        ),
    )


def test_sync_training_example_exports_approved_document(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = Repository(settings.database_path)
    strategy = StrategyService(settings, repository)
    training = TrainingService(settings, repository, strategy)

    approved_file = tmp_path / "approved.pdf"
    approved_json = tmp_path / "approved.json"
    approved_file.write_bytes(b"fake-pdf")
    approved_json.write_text('{"document_id":"inv_1"}', encoding="utf-8")

    repository.upsert_document(
        {
            "id": "inv_1",
            "po_box": "1001",
            "original_filename": "invoice.pdf",
            "current_file_path": str(approved_file),
            "current_json_path": str(approved_json),
            "status": "approved",
            "vendor": "Acme Utility",
            "confidence": 1.0,
            "extraction": {"vendor": "Acme Utility", "model_source": "legacy_local"},
            "verification": {},
            "alignment": {"field_alignments": {}, "approved_file_path": str(approved_file)},
        }
    )

    example = training.sync_training_example(repository.get_document("inv_1"))

    assert example is not None
    assert Path(example["file_path"]).exists()
    assert Path(example["json_path"]).exists()
    assert Path(example["alignment_path"]).exists()


def test_create_training_run_records_results(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = Repository(settings.database_path)
    strategy = StrategyService(settings, repository)
    training = TrainingService(settings, repository, strategy)

    approved_file = tmp_path / "approved.pdf"
    approved_json = tmp_path / "approved.json"
    approved_file.write_bytes(b"fake-pdf")
    approved_json.write_text('{"document_id":"inv_2"}', encoding="utf-8")
    repository.upsert_document(
        {
            "id": "inv_2",
            "po_box": "1001",
            "original_filename": "invoice.pdf",
            "current_file_path": str(approved_file),
            "current_json_path": str(approved_json),
            "status": "approved",
            "vendor": "Acme Utility",
            "confidence": 1.0,
            "extraction": {"vendor": "Acme Utility", "model_source": "legacy_local"},
            "verification": {},
            "alignment": {"field_alignments": {}, "approved_file_path": str(approved_file)},
        }
    )

    def fake_evaluate(document_ids: list[str], strategy_name: str) -> dict:
        score = 0.9 if strategy_name == "ppstruct_layoutlm_qwen" else 0.7
        return {"document_count": len(document_ids), "average_score": score, "documents": []}

    monkeypatch.setattr(training, "evaluate_documents", fake_evaluate)

    run = training.create_training_run(
        name="Candidate benchmark",
        strategy_name="ppstruct_layoutlm_qwen",
        document_ids=["inv_2"],
    )

    assert run["results"]["improved"] is True
    assert Path(run["corpus_path"]).exists()
