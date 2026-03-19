from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import httpx

from .config import Settings
from .utils import normalize_vendor, utcnow


LOGGER = logging.getLogger("bfsai.client_lookup")

NICKNAME_EQUIVALENTS = {
    "michael": {"mike", "mikey", "michael"},
    "mike": {"mike", "mikey", "michael"},
    "susanne": {"susanne", "susan", "suzanne", "sue", "susie"},
    "susan": {"susanne", "susan", "suzanne", "sue", "susie"},
}

PERSON_BLOCKLIST = {
    "account",
    "associates",
    "bank",
    "club",
    "company",
    "corp",
    "corporation",
    "estate",
    "family",
    "foundation",
    "fund",
    "group",
    "holdings",
    "homeowners",
    "inc",
    "investment",
    "investments",
    "llc",
    "lp",
    "partners",
    "partnership",
    "pllc",
    "properties",
    "residence",
    "revocable",
    "services",
    "society",
    "trust",
}


@dataclass(frozen=True, slots=True)
class ClientMatch:
    po_box: str
    client_id: int
    client_name: str
    matched_alias: str
    score: int


class ClientLookupService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_path = settings.extraction.runtime_dir / "clients_minimal_cache.json"

    def match_po_box(self, text: str) -> ClientMatch | None:
        normalized_text = normalize_vendor(text)
        if not normalized_text:
            return None
        text_tokens = set(normalized_text.split())
        best: ClientMatch | None = None
        for record in self._load_client_index():
            po_box = str(record.get("pobox") or "").strip()
            if not po_box:
                continue
            client_id = int(record.get("id") or 0)
            client_name = self._display_name_for_record(record) or po_box
            for alias in self._aliases_for_record(record):
                score = self._score_alias(alias, normalized_text, text_tokens)
                if score <= 0:
                    continue
                if best is None or score > best.score:
                    best = ClientMatch(
                        po_box=po_box,
                        client_id=client_id,
                        client_name=client_name,
                        matched_alias=alias,
                        score=score,
                    )
        return best

    def list_clients(self) -> list[dict[str, Any]]:
        records = []
        for record in self._load_client_index():
            po_box = str(record.get("pobox") or "").strip()
            if not po_box:
                continue
            display_name = self._display_name_for_record(record) or po_box
            label = f"{display_name} - {po_box}"
            search_terms = [label, po_box]
            search_terms.extend(self._aliases_for_record(record))
            records.append(
                {
                    "po_box": po_box,
                    "label": label,
                    "display_name": display_name,
                    "name": str(record.get("name") or "").strip(),
                    "friendly_name": str(record.get("friendly_name") or "").strip(),
                    "client_contact_name": str(record.get("client_contact_name") or "").strip(),
                    "search_text": " ".join(term for term in search_terms if term).strip(),
                }
            )
        return sorted(records, key=lambda item: (item["label"].lower(), item["po_box"]))

    def client_name_for_po_box(self, po_box: str) -> str:
        lookup = str(po_box).strip()
        for record in self._load_client_index():
            if str(record.get("pobox") or "").strip() != lookup:
                continue
            return self._display_name_for_record(record) or lookup
        return lookup

    def _display_name_for_record(self, record: dict[str, Any]) -> str:
        contact_name = self._clean_whitespace(record.get("client_contact_name"))
        base_name = contact_name or self._format_name_for_display(self._clean_whitespace(record.get("name")))
        friendly_name = self._clean_whitespace(record.get("friendly_name"))
        if not base_name:
            base_name = friendly_name
        if not base_name:
            return ""
        if friendly_name:
            normalized_base = normalize_vendor(base_name)
            normalized_friendly = normalize_vendor(friendly_name)
            if normalized_friendly and normalized_friendly not in normalized_base:
                base_name = f"{base_name} ({friendly_name})"
        return base_name

    def _format_name_for_display(self, value: str) -> str:
        if not value:
            return ""
        if "," in value or any(char.isdigit() for char in value):
            return value
        tokens = value.split()
        if len(tokens) < 2 or len(tokens) > 4:
            return value
        normalized_tokens = {token.strip(".,").lower() for token in tokens}
        if normalized_tokens & PERSON_BLOCKLIST:
            return value
        last_name = tokens[-1]
        first_names = " ".join(tokens[:-1]).strip()
        return f"{last_name}, {first_names}" if first_names else value

    def _clean_whitespace(self, value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    def _aliases_for_record(self, record: dict[str, Any]) -> list[str]:
        aliases: list[str] = []
        for key in ("name", "friendly_name"):
            value = str(record.get(key) or "").strip()
            if value:
                aliases.append(value)
        client_contact_name = str(record.get("client_contact_name") or "").strip()
        if client_contact_name:
            aliases.extend(self._expand_contact_alias(client_contact_name))
        for contact in record.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            for key in ("friendly_name", "display_name"):
                value = str(contact.get(key) or "").strip()
                if value:
                    aliases.append(value)
            first = str(contact.get("first_name") or "").strip()
            last = str(contact.get("last_name") or "").strip()
            if first or last:
                aliases.append(" ".join(part for part in (first, last) if part).strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            normalized = normalize_vendor(alias)
            if len(normalized) < 5 or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(alias)
        return deduped

    def _expand_contact_alias(self, value: str) -> list[str]:
        if "," not in value:
            return [value]
        last, first = [part.strip() for part in value.split(",", 1)]
        reordered = " ".join(part for part in (first, last) if part).strip()
        aliases = [reordered] if reordered else []
        aliases.append(value)
        return aliases

    def _score_alias(self, alias: str, normalized_text: str, text_tokens: set[str]) -> int:
        normalized_alias = normalize_vendor(alias)
        alias_tokens = [token for token in normalized_alias.split() if len(token) > 1]
        if len(alias_tokens) < 2:
            return 0
        if normalized_alias in normalized_text:
            return 200 + len(normalized_alias)
        overlap = sum(1 for token in alias_tokens if token in text_tokens)
        if overlap == len(alias_tokens):
            return 120 + overlap * 10
        if overlap >= 3 and overlap >= len(alias_tokens) - 1:
            return 80 + overlap * 8
        person_score = 0 if "," in alias else self._score_person_name(alias_tokens, text_tokens)
        if person_score > 0:
            return person_score
        return 0

    def _score_person_name(self, alias_tokens: list[str], text_tokens: set[str]) -> int:
        if len(alias_tokens) < 2:
            return 0
        first_name = alias_tokens[0]
        last_name = alias_tokens[-1]
        if last_name not in text_tokens:
            return 0
        if self._token_matches(first_name, text_tokens):
            return 135
        return 0

    def _token_matches(self, candidate: str, text_tokens: set[str]) -> bool:
        variants = NICKNAME_EQUIVALENTS.get(candidate, {candidate})
        if any(variant in text_tokens for variant in variants):
            return True
        for token in text_tokens:
            if len(token) < 3:
                continue
            if SequenceMatcher(None, candidate, token).ratio() >= 0.82:
                return True
        return False

    def _load_client_index(self) -> list[dict[str, Any]]:
        cached = self._read_cache()
        if self._cache_is_fresh(cached):
            return list(cached.get("records") or [])
        refreshed = self._refresh_client_index()
        if refreshed:
            return refreshed
        return list(cached.get("records") or [])

    def _cache_is_fresh(self, payload: dict[str, Any]) -> bool:
        fetched_at = float(payload.get("fetched_at_epoch") or 0)
        if fetched_at <= 0:
            return False
        age_seconds = max(0.0, time.time() - fetched_at)
        return age_seconds < self.settings.rails.client_cache_ttl_seconds

    def _read_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _refresh_client_index(self) -> list[dict[str, Any]]:
        auth_name = self.settings.rails.auth_name.strip()
        auth_password = self.settings.rails.auth_password.strip()
        if not auth_name or not auth_password:
            return []

        base_url = self.settings.rails.base_url.rstrip("/")
        try:
            with httpx.Client(base_url=base_url, timeout=self.settings.rails.timeout_seconds, follow_redirects=True) as client:
                token = self._login(client, auth_name, auth_password)
                if not token:
                    return []
                records = self._fetch_clients_minimal(client, token)
        except Exception as error:
            LOGGER.warning("client lookup refresh failed: %s", error)
            return []

        payload = {
            "fetched_at": utcnow(),
            "fetched_at_epoch": time.time(),
            "records": records,
        }
        self.cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return records

    def _login(self, client: httpx.Client, auth_name: str, auth_password: str) -> str | None:
        response = client.post(
            self.settings.rails.login_path,
            data={"name": auth_name, "password": auth_password},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("token") or "").strip()
        return token or None

    def _fetch_clients_minimal(self, client: httpx.Client, token: str) -> list[dict[str, Any]]:
        headers = {
            "Accept": "application/json",
            "Authorization": f'Token token="{token}"',
        }
        response = client.get(
            self.settings.rails.clients_minimal_path,
            params={"start": 0, "limit": 10000},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        return [record for record in data if isinstance(record, dict)]
