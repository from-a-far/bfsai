from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import Settings, StrategyProfile
from .repository import Repository
from .utils import utcnow


class StrategyService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.runtime_file = self.settings.extraction.runtime_dir / "active_strategy.json"

    def list_profiles(self) -> list[dict[str, Any]]:
        active_name = self.active_strategy_name()
        return [
            asdict(profile) | {"is_active": name == active_name}
            for name, profile in self.settings.extraction.strategies.items()
        ]

    def active_strategy_name(self) -> str:
        if self.runtime_file.exists():
            try:
                payload = json.loads(self.runtime_file.read_text(encoding="utf-8"))
                strategy_name = str(payload.get("active_strategy") or "").strip()
                if strategy_name in self.settings.extraction.strategies:
                    return strategy_name
            except Exception:
                pass
        return self.settings.extraction.active_strategy

    def active_profile(self) -> StrategyProfile:
        name = self.active_strategy_name()
        return self.settings.extraction.strategies.get(name) or next(iter(self.settings.extraction.strategies.values()))

    def activate_strategy(
        self,
        strategy_name: str,
        training_run_id: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if strategy_name not in self.settings.extraction.strategies:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        profile = self.settings.extraction.strategies[strategy_name]
        payload = {
            "active_strategy": strategy_name,
            "label": profile.label,
            "kind": profile.kind,
            "activated_at": utcnow(),
            "training_run_id": training_run_id,
            "notes": notes,
        }
        self.runtime_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self.repository.record_strategy_activation(strategy_name, training_run_id=training_run_id, notes=notes)
        return payload

    def status(self) -> dict[str, Any]:
        profile = self.active_profile()
        return {
            "active_strategy": profile.name,
            "label": profile.label,
            "kind": profile.kind,
            "description": profile.description,
            "runtime_file": str(self.runtime_file),
        }

