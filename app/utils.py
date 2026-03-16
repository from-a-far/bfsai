from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def short_uid(prefix: str = "inv") -> str:
    token = base64.b32encode(uuid.uuid4().bytes).decode("ascii").rstrip("=").lower()
    return f"{prefix}_{token[:16]}"


def normalize_vendor(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z0-9./#-]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


PO_BOX_PATTERN = re.compile(r"\b(5010\d{3})\b")


def detect_po_box(text: str | None) -> str | None:
    if not text:
        return None
    match = PO_BOX_PATTERN.search(text)
    return match.group(1) if match else None


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) else round(number, 2)
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return round(float(Decimal(cleaned)), 2)
    except (InvalidOperation, ValueError):
        return None


def compact_excerpt(text: str, limit: int = 1600) -> str:
    squashed = re.sub(r"\s+", " ", text).strip()
    return squashed[:limit]
