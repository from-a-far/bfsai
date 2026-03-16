from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class OllamaSettings:
    enabled: bool = True
    base_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    timeout_seconds: int = 90


@dataclass(slots=True)
class ThresholdSettings:
    verified_confidence: float = 0.9
    flagged_amount: float = 50000.0
    total_delta_tolerance: float = 1.0


@dataclass(slots=True)
class RailsSettings:
    enabled: bool = False
    base_url: str = "http://host.docker.internal:3000"
    endpoint_path: str = "/api/invoice_ingestions"
    api_token: str = ""
    timeout_seconds: int = 30
    auto_ingest_approved: bool = False


@dataclass(slots=True)
class StrategyProfile:
    name: str
    label: str
    kind: str
    description: str = ""
    enabled: bool = True
    ppstructure_command: str = ""
    layoutlm_model: str = ""
    qwen_base_url: str = ""
    qwen_model: str = ""
    train_command: str = ""
    eval_command: str = ""


@dataclass(slots=True)
class ExtractionSettings:
    active_strategy: str
    corpus_dir: Path
    runs_dir: Path
    runtime_dir: Path
    strategies: dict[str, StrategyProfile]


def _default_extraction_settings() -> ExtractionSettings:
    return ExtractionSettings(
        active_strategy="legacy_local",
        corpus_dir=Path("./storage/training_corpus"),
        runs_dir=Path("./storage/training_runs"),
        runtime_dir=Path("./storage/runtime"),
        strategies={
            "legacy_local": StrategyProfile(
                name="legacy_local",
                label="Current Local System",
                kind="legacy",
                description="Existing OCR + learning + local LLM workflow.",
                enabled=True,
            ),
            "ppstruct_layoutlm_qwen": StrategyProfile(
                name="ppstruct_layoutlm_qwen",
                label="PP-StructureV3 + LayoutLMv3 + Qwen2.5-VL-7B",
                kind="experimental",
                description="Experimental layout-aware stack with document understanding and vision-language extraction.",
                enabled=True,
                layoutlm_model="LayoutLMv3",
                qwen_base_url="",
                qwen_model="Qwen2.5-VL-7B",
            ),
        },
    )


@dataclass(slots=True)
class Settings:
    watch_root: Path
    database_path: Path
    poll_seconds: int
    ollama: OllamaSettings
    thresholds: ThresholdSettings
    rails: RailsSettings
    extraction: ExtractionSettings = field(default_factory=_default_extraction_settings)
    scan_root: Path = Path("./server/scans")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings() -> Settings:
    config_path = Path(os.environ.get("BFSAI_CONFIG", "config/settings.yaml"))
    raw = _read_yaml(config_path)
    watch_root = Path(raw.get("watch_root", "/data/invoices"))
    scan_root = Path(raw.get("scan_root", "./server/scans"))
    database_path = Path(raw.get("database_path", "/data/storage/bfsai.db"))
    ollama = raw.get("ollama", {})
    thresholds = raw.get("thresholds", {})
    rails = raw.get("rails", {})
    extraction = raw.get("extraction", {})
    runtime_dir = Path(extraction.get("runtime_dir", "./storage/runtime"))
    strategy_override_path = runtime_dir / "active_strategy.json"
    default_strategies = {
        "legacy_local": {
            "label": "Current Local System",
            "kind": "legacy",
            "description": "Existing OCR + learning + local LLM workflow.",
            "enabled": True,
        },
        "ppstruct_layoutlm_qwen": {
            "label": "PP-StructureV3 + LayoutLMv3 + Qwen2.5-VL-7B",
            "kind": "experimental",
            "description": "Experimental layout-aware stack with document understanding and vision-language extraction.",
            "enabled": True,
            "ppstructure_command": "",
            "layoutlm_model": "LayoutLMv3",
            "qwen_base_url": "http://127.0.0.1:8002",
            "qwen_model": "Qwen2.5-VL-7B",
            "train_command": "",
            "eval_command": "",
        },
    }
    strategies_raw = default_strategies | dict(extraction.get("strategies", {}))
    active_strategy = str(extraction.get("active_strategy", "legacy_local"))
    if strategy_override_path.exists():
        try:
            override = _read_yaml(strategy_override_path)
            active_strategy = str(override.get("active_strategy", active_strategy))
        except Exception:
            pass
    settings = Settings(
        watch_root=watch_root,
        database_path=database_path,
        poll_seconds=int(raw.get("poll_seconds", 3)),
        ollama=OllamaSettings(
            enabled=bool(ollama.get("enabled", True)),
            base_url=str(ollama.get("base_url", "http://ollama:11434")),
            model=str(ollama.get("model", "llama3.2:3b")),
            timeout_seconds=int(ollama.get("timeout_seconds", 90)),
        ),
        thresholds=ThresholdSettings(
            verified_confidence=float(thresholds.get("verified_confidence", 0.9)),
            flagged_amount=float(thresholds.get("flagged_amount", 50000)),
            total_delta_tolerance=float(thresholds.get("total_delta_tolerance", 1.0)),
        ),
        rails=RailsSettings(
            enabled=bool(rails.get("enabled", False)),
            base_url=str(rails.get("base_url", "http://host.docker.internal:3000")),
            endpoint_path=str(rails.get("endpoint_path", "/api/invoice_ingestions")),
            api_token=str(rails.get("api_token", "")),
            timeout_seconds=int(rails.get("timeout_seconds", 30)),
            auto_ingest_approved=bool(rails.get("auto_ingest_approved", False)),
        ),
        extraction=ExtractionSettings(
            active_strategy=active_strategy,
            corpus_dir=Path(extraction.get("corpus_dir", "./storage/training_corpus")),
            runs_dir=Path(extraction.get("runs_dir", "./storage/training_runs")),
            runtime_dir=runtime_dir,
            strategies={
                name: StrategyProfile(
                    name=name,
                    label=str(payload.get("label", name)),
                    kind=str(payload.get("kind", "legacy")),
                    description=str(payload.get("description", "")),
                    enabled=bool(payload.get("enabled", True)),
                    ppstructure_command=str(payload.get("ppstructure_command", "")),
                    layoutlm_model=str(payload.get("layoutlm_model", "")),
                    qwen_base_url=str(payload.get("qwen_base_url", "")),
                    qwen_model=str(payload.get("qwen_model", "")),
                    train_command=str(payload.get("train_command", "")),
                    eval_command=str(payload.get("eval_command", "")),
                )
                for name, payload in strategies_raw.items()
            },
        ),
        scan_root=scan_root,
    )
    settings.scan_root.mkdir(parents=True, exist_ok=True)
    settings.watch_root.mkdir(parents=True, exist_ok=True)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.extraction.corpus_dir.mkdir(parents=True, exist_ok=True)
    settings.extraction.runs_dir.mkdir(parents=True, exist_ok=True)
    settings.extraction.runtime_dir.mkdir(parents=True, exist_ok=True)
    return settings
