from __future__ import annotations

import os
from dataclasses import dataclass
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
class Settings:
    watch_root: Path
    database_path: Path
    poll_seconds: int
    ollama: OllamaSettings
    thresholds: ThresholdSettings
    rails: RailsSettings


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings() -> Settings:
    config_path = Path(os.environ.get("BFSAI_CONFIG", "config/settings.yaml"))
    raw = _read_yaml(config_path)
    watch_root = Path(raw.get("watch_root", "/data/invoices"))
    database_path = Path(raw.get("database_path", "/data/storage/bfsai.db"))
    ollama = raw.get("ollama", {})
    thresholds = raw.get("thresholds", {})
    rails = raw.get("rails", {})
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
    )
    settings.watch_root.mkdir(parents=True, exist_ok=True)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
