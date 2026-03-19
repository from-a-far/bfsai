import json
from pathlib import Path

from app.config import ExtractionSettings, OllamaSettings, RailsSettings, Settings, StrategyProfile, ThresholdSettings
from app.extractor import Extractor, resolve_tesseract_cmd


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


def test_extract_uses_runtime_strategy_override(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        watch_root=tmp_path / "watch",
        database_path=tmp_path / "bfsai-test.db",
        poll_seconds=3,
        ollama=OllamaSettings(enabled=False),
        thresholds=ThresholdSettings(),
        rails=RailsSettings(),
        extraction=ExtractionSettings(
            active_strategy="legacy_local",
            corpus_dir=tmp_path / "corpus",
            runs_dir=tmp_path / "runs",
            runtime_dir=runtime_dir,
            strategies={
                "legacy_local": StrategyProfile(name="legacy_local", label="Legacy", kind="legacy"),
                "ppstruct_layoutlm_qwen": StrategyProfile(
                    name="ppstruct_layoutlm_qwen",
                    label="Experimental",
                    kind="experimental",
                ),
            },
        ),
    )
    extractor = Extractor(settings)
    (runtime_dir / "active_strategy.json").write_text(
        json.dumps({"active_strategy": "ppstruct_layoutlm_qwen"}),
        encoding="utf-8",
    )

    extraction = extractor.extract("1001", "Statement for service account", {})

    assert extraction.model_source == "ppstruct_layoutlm_qwen:fallback"


def test_resolve_tesseract_cmd_uses_env_override(monkeypatch, tmp_path: Path) -> None:
    candidate = tmp_path / "tesseract"
    candidate.write_text("", encoding="utf-8")
    candidate.chmod(0o755)
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setenv("BFSAI_TESSERACT_CMD", str(candidate))

    resolved = resolve_tesseract_cmd()

    assert resolved == str(candidate)
