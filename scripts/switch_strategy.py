#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from app.config import load_settings
from app.repository import Repository
from app.strategy import StrategyService


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: scripts/switch_strategy.py <strategy_name>")
    strategy_name = sys.argv[1].strip()
    settings = load_settings()
    repository = Repository(settings.database_path)
    service = StrategyService(settings, repository)
    payload = service.activate_strategy(
        strategy_name,
        notes="Switched via scripts/switch_strategy.py",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

