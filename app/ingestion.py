from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .repository import Repository
from .utils import utcnow


class RailsIngestionService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository

    def ingest_document(self, document: dict[str, Any]) -> dict[str, Any]:
        attempts = int(document.get("ingestion_attempts") or 0) + 1
        last_attempt_at = utcnow()
        payload = {
            "document_id": document["id"],
            "po_box": document["po_box"],
            "status": document["status"],
            "source_file_path": document["current_file_path"],
            "json_file_path": document.get("current_json_path"),
            "extraction": document["extraction"],
            "verification": document["verification"],
            "alignment": document["alignment"],
        }
        if not self.settings.rails.enabled:
            self.repository.record_ingestion_event(
                document["id"],
                status="skipped",
                request_payload=payload,
                error_message="Rails ingestion is disabled",
            )
            self.repository.update_ingestion_state(
                document["id"],
                status="pending",
                attempts=attempts,
                error_message="Rails ingestion is disabled",
                last_attempt_at=last_attempt_at,
            )
            return {"status": "skipped", "error_message": "Rails ingestion is disabled"}

        headers = {"Content-Type": "application/json"}
        if self.settings.rails.api_token:
            headers["Authorization"] = f"Bearer {self.settings.rails.api_token}"
        url = f"{self.settings.rails.base_url.rstrip('/')}{self.settings.rails.endpoint_path}"
        try:
            response = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.settings.rails.timeout_seconds,
            )
            response.raise_for_status()
            response_body = response.text[:4000]
            ingested_at = utcnow()
            self.repository.record_ingestion_event(
                document["id"],
                status="ingested",
                request_payload=payload,
                response_status=response.status_code,
                response_body=response_body,
            )
            self.repository.update_ingestion_state(
                document["id"],
                status="ingested",
                attempts=attempts,
                ingested_at=ingested_at,
                last_attempt_at=last_attempt_at,
            )
            return {
                "status": "ingested",
                "response_status": response.status_code,
                "ingested_at": ingested_at,
            }
        except Exception as error:
            self.repository.record_ingestion_event(
                document["id"],
                status="failed",
                request_payload=payload,
                error_message=str(error),
            )
            self.repository.update_ingestion_state(
                document["id"],
                status="failed",
                attempts=attempts,
                error_message=str(error),
                last_attempt_at=last_attempt_at,
            )
            return {"status": "failed", "error_message": str(error)}
