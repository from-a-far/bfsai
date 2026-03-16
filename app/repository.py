from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .utils import json_dumps, utcnow


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  po_box TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  current_file_path TEXT NOT NULL,
  current_json_path TEXT,
  status TEXT NOT NULL,
  vendor TEXT,
  invoice_number TEXT,
  invoice_date TEXT,
  subtotal REAL,
  tax REAL,
  total REAL,
  currency TEXT,
  payment_terms TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  extraction_json TEXT NOT NULL,
  verification_json TEXT NOT NULL,
  alignment_json TEXT NOT NULL,
  error_message TEXT,
  review_notes TEXT,
  ingestion_status TEXT NOT NULL DEFAULT 'pending',
  ingestion_attempts INTEGER NOT NULL DEFAULT 0,
  ingestion_error_message TEXT,
  last_ingestion_attempt_at TEXT,
  ingested_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT NOT NULL,
  po_box TEXT NOT NULL,
  field_name TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor_profiles (
  po_box TEXT NOT NULL,
  normalized_vendor TEXT NOT NULL,
  display_vendor TEXT NOT NULL,
  approved_count INTEGER NOT NULL DEFAULT 0,
  correction_count INTEGER NOT NULL DEFAULT 0,
  confirmed_fields_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (po_box, normalized_vendor)
);

CREATE TABLE IF NOT EXISTS vendor_field_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  po_box TEXT NOT NULL,
  normalized_vendor TEXT NOT NULL,
  field_name TEXT NOT NULL,
  page_number INTEGER NOT NULL,
  page_count INTEGER NOT NULL DEFAULT 1,
  normalized_bbox_json TEXT NOT NULL,
  sample_value TEXT,
  sample_count INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT NOT NULL,
  action TEXT NOT NULL,
  notes TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT NOT NULL,
  status TEXT NOT NULL,
  response_status INTEGER,
  request_json TEXT NOT NULL DEFAULT '{}',
  response_body TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_examples (
  document_id TEXT PRIMARY KEY,
  po_box TEXT NOT NULL,
  vendor TEXT,
  status TEXT NOT NULL DEFAULT 'ready',
  strategy_source TEXT NOT NULL,
  example_dir TEXT NOT NULL,
  file_path TEXT NOT NULL,
  json_path TEXT NOT NULL,
  alignment_path TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_runs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  strategy_name TEXT NOT NULL,
  status TEXT NOT NULL,
  selected_document_ids_json TEXT NOT NULL,
  example_count INTEGER NOT NULL DEFAULT 0,
  corpus_path TEXT,
  results_json TEXT NOT NULL DEFAULT '{}',
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  activated_at TEXT
);

CREATE TABLE IF NOT EXISTS strategy_activations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_name TEXT NOT NULL,
  training_run_id TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_column(connection, "documents", "review_notes", "TEXT")
            self._ensure_column(connection, "documents", "ingestion_status", "TEXT NOT NULL DEFAULT 'pending'")
            self._ensure_column(connection, "documents", "ingestion_attempts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "documents", "ingestion_error_message", "TEXT")
            self._ensure_column(connection, "documents", "last_ingestion_attempt_at", "TEXT")
            self._ensure_column(connection, "documents", "ingested_at", "TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_vendor_field_profiles_lookup
                ON vendor_field_profiles (po_box, normalized_vendor, field_name, page_count, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_training_examples_status
                ON training_examples (status, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_training_runs_strategy
                ON training_runs (strategy_name, created_at DESC)
                """
            )

    def _ensure_column(self, connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def upsert_document(self, record: dict[str, Any]) -> None:
        now = utcnow()
        payload = {
            "id": record["id"],
            "po_box": record["po_box"],
            "original_filename": record["original_filename"],
            "current_file_path": record["current_file_path"],
            "current_json_path": record.get("current_json_path"),
            "status": record["status"],
            "vendor": record.get("vendor"),
            "invoice_number": record.get("invoice_number"),
            "invoice_date": record.get("invoice_date"),
            "subtotal": record.get("subtotal"),
            "tax": record.get("tax"),
            "total": record.get("total"),
            "currency": record.get("currency"),
            "payment_terms": record.get("payment_terms"),
            "confidence": record.get("confidence", 0),
            "extraction_json": json_dumps(record.get("extraction", {})),
            "verification_json": json_dumps(record.get("verification", {})),
            "alignment_json": json_dumps(record.get("alignment", {})),
            "error_message": record.get("error_message"),
            "review_notes": record.get("review_notes"),
            "ingestion_status": record.get("ingestion_status", "pending"),
            "ingestion_attempts": record.get("ingestion_attempts", 0),
            "ingestion_error_message": record.get("ingestion_error_message"),
            "last_ingestion_attempt_at": record.get("last_ingestion_attempt_at"),
            "ingested_at": record.get("ingested_at"),
            "created_at": record.get("created_at", now),
            "updated_at": now,
            "confirmed_at": record.get("confirmed_at"),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                  id, po_box, original_filename, current_file_path, current_json_path, status,
                  vendor, invoice_number, invoice_date, subtotal, tax, total, currency,
                  payment_terms, confidence, extraction_json, verification_json, alignment_json,
                  error_message, review_notes, ingestion_status, ingestion_attempts,
                  ingestion_error_message, last_ingestion_attempt_at, ingested_at,
                  created_at, updated_at, confirmed_at
                ) VALUES (
                  :id, :po_box, :original_filename, :current_file_path, :current_json_path, :status,
                  :vendor, :invoice_number, :invoice_date, :subtotal, :tax, :total, :currency,
                  :payment_terms, :confidence, :extraction_json, :verification_json, :alignment_json,
                  :error_message, :review_notes, :ingestion_status, :ingestion_attempts,
                  :ingestion_error_message, :last_ingestion_attempt_at, :ingested_at,
                  :created_at, :updated_at, :confirmed_at
                )
                ON CONFLICT(id) DO UPDATE SET
                  po_box=excluded.po_box,
                  original_filename=excluded.original_filename,
                  current_file_path=excluded.current_file_path,
                  current_json_path=excluded.current_json_path,
                  status=excluded.status,
                  vendor=excluded.vendor,
                  invoice_number=excluded.invoice_number,
                  invoice_date=excluded.invoice_date,
                  subtotal=excluded.subtotal,
                  tax=excluded.tax,
                  total=excluded.total,
                  currency=excluded.currency,
                  payment_terms=excluded.payment_terms,
                  confidence=excluded.confidence,
                  extraction_json=excluded.extraction_json,
                  verification_json=excluded.verification_json,
                  alignment_json=excluded.alignment_json,
                  error_message=excluded.error_message,
                  review_notes=excluded.review_notes,
                  ingestion_status=excluded.ingestion_status,
                  ingestion_attempts=excluded.ingestion_attempts,
                  ingestion_error_message=excluded.ingestion_error_message,
                  last_ingestion_attempt_at=excluded.last_ingestion_attempt_at,
                  ingested_at=excluded.ingested_at,
                  updated_at=excluded.updated_at,
                  confirmed_at=excluded.confirmed_at
                """,
                payload,
            )

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def delete_document(self, document_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM corrections WHERE document_id = ?", (document_id,))
            connection.execute("DELETE FROM review_events WHERE document_id = ?", (document_id,))
            connection.execute("DELETE FROM ingestion_events WHERE document_id = ?", (document_id,))
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def list_documents(
        self,
        status: str | None = None,
        po_box: str | None = None,
        search: str | None = None,
        ingestion_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM documents"
        params: list[Any] = []
        clauses: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if po_box:
            clauses.append("po_box = ?")
            params.append(po_box)
        if ingestion_status:
            clauses.append("ingestion_status = ?")
            params.append(ingestion_status)
        if search:
            clauses.append("(id LIKE ? OR original_filename LIKE ? OR COALESCE(vendor, '') LIKE ? OR COALESCE(invoice_number, '') LIKE ?)")
            search_value = f"%{search}%"
            params.extend([search_value, search_value, search_value, search_value])
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_document(row) for row in rows]

    def list_documents_for_learning(
        self,
        po_box: str,
        statuses: tuple[str, ...] = ("approved",),
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM documents
                WHERE po_box = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC, created_at DESC, id DESC
                """,
                (po_box, *statuses),
            ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def list_documents_by_ids(self, document_ids: list[str]) -> list[dict[str, Any]]:
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM documents WHERE id IN ({placeholders})",
                tuple(document_ids),
            ).fetchall()
        documents = {row["id"]: self._row_to_document(row) for row in rows}
        return [documents[document_id] for document_id in document_ids if document_id in documents]

    def review_queue_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM documents
                WHERE status IN ('review', 'verified', 'error')
                ORDER BY updated_at DESC, created_at DESC, id DESC
                """
            ).fetchall()
        return [row["id"] for row in rows]

    def record_correction(
        self,
        document_id: str,
        po_box: str,
        field_name: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO corrections (document_id, po_box, field_name, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    po_box,
                    field_name,
                    json.dumps(old_value, default=str),
                    json.dumps(new_value, default=str),
                    utcnow(),
                ),
            )

    def record_review_event(
        self,
        document_id: str,
        action: str,
        notes: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO review_events (document_id, action, notes, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    action,
                    notes,
                    json_dumps(payload or {}),
                    utcnow(),
                ),
            )

    def list_review_events(self, document_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_events WHERE document_id = ? ORDER BY created_at DESC, id DESC",
                (document_id,),
            ).fetchall()
        return [
            dict(row) | {"payload": json.loads(row["payload_json"] or "{}")}
            for row in rows
        ]

    def record_ingestion_event(
        self,
        document_id: str,
        status: str,
        request_payload: dict[str, Any],
        response_status: int | None = None,
        response_body: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_events (
                  document_id, status, response_status, request_json, response_body, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    status,
                    response_status,
                    json_dumps(request_payload),
                    response_body,
                    error_message,
                    utcnow(),
                ),
            )

    def list_ingestion_events(self, document_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ingestion_events WHERE document_id = ? ORDER BY created_at DESC, id DESC",
                (document_id,),
            ).fetchall()
        return [
            dict(row) | {"request": json.loads(row["request_json"] or "{}")}
            for row in rows
        ]

    def update_ingestion_state(
        self,
        document_id: str,
        status: str,
        attempts: int,
        error_message: str | None = None,
        ingested_at: str | None = None,
        last_attempt_at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE documents
                SET ingestion_status = ?,
                    ingestion_attempts = ?,
                    ingestion_error_message = ?,
                    ingested_at = ?,
                    last_ingestion_attempt_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    attempts,
                    error_message,
                    ingested_at,
                    last_attempt_at,
                    utcnow(),
                    document_id,
                ),
            )

    def get_vendor_profiles(self, po_box: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM vendor_profiles
                WHERE po_box = ?
                ORDER BY approved_count DESC, updated_at DESC
                """,
                (po_box,),
            ).fetchall()
        return [dict(row) | {"confirmed_fields": json.loads(row["confirmed_fields_json"])} for row in rows]

    def upsert_vendor_profile(
        self,
        po_box: str,
        normalized_vendor: str,
        display_vendor: str,
        approved_count: int,
        correction_count: int,
        confirmed_fields: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vendor_profiles (
                  po_box, normalized_vendor, display_vendor, approved_count, correction_count,
                  confirmed_fields_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(po_box, normalized_vendor) DO UPDATE SET
                  display_vendor=excluded.display_vendor,
                  approved_count=excluded.approved_count,
                  correction_count=excluded.correction_count,
                  confirmed_fields_json=excluded.confirmed_fields_json,
                  updated_at=excluded.updated_at
                """,
                (
                    po_box,
                    normalized_vendor,
                    display_vendor,
                    approved_count,
                    correction_count,
                    json_dumps(confirmed_fields),
                    utcnow(),
                ),
            )

    def get_vendor_field_profiles(
        self,
        po_box: str,
        normalized_vendor: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM vendor_field_profiles
                WHERE po_box = ? AND normalized_vendor = ?
                ORDER BY sample_count DESC, updated_at DESC, id DESC
                """,
                (po_box, normalized_vendor),
            ).fetchall()
        return [
            dict(row)
            | {
                "normalized_bbox": json.loads(row["normalized_bbox_json"] or "{}"),
                "sample_value": json.loads(row["sample_value"]) if row["sample_value"] else None,
            }
            for row in rows
        ]

    def upsert_vendor_field_profile(
        self,
        po_box: str,
        normalized_vendor: str,
        field_name: str,
        page_number: int,
        page_count: int,
        normalized_bbox: dict[str, Any],
        sample_value: Any,
    ) -> None:
        bbox_json = json_dumps(normalized_bbox)
        sample_value_json = json.dumps(sample_value, default=str) if sample_value not in (None, "") else None
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id, sample_count
                FROM vendor_field_profiles
                WHERE po_box = ?
                  AND normalized_vendor = ?
                  AND field_name = ?
                  AND page_number = ?
                  AND page_count = ?
                  AND normalized_bbox_json = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (po_box, normalized_vendor, field_name, page_number, page_count, bbox_json),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE vendor_field_profiles
                    SET sample_count = ?,
                        sample_value = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(existing["sample_count"] or 0) + 1,
                        sample_value_json,
                        utcnow(),
                        existing["id"],
                    ),
                )
                return
            connection.execute(
                """
                INSERT INTO vendor_field_profiles (
                  po_box, normalized_vendor, field_name, page_number, page_count,
                  normalized_bbox_json, sample_value, sample_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    po_box,
                    normalized_vendor,
                    field_name,
                    page_number,
                    page_count,
                    bbox_json,
                    sample_value_json,
                    1,
                    utcnow(),
                ),
            )

    def dashboard_stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN status = 'review' THEN 1 ELSE 0 END) AS review_count,
                  SUM(CASE WHEN status = 'verified' THEN 1 ELSE 0 END) AS verified_count,
                  SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count,
                  SUM(CASE WHEN ingestion_status = 'ingested' THEN 1 ELSE 0 END) AS ingested_count,
                  SUM(CASE WHEN ingestion_status = 'failed' THEN 1 ELSE 0 END) AS failed_ingestion_count,
                  ROUND(AVG(confidence), 3) AS avg_confidence
                FROM documents
                """
            ).fetchone()
            correction_rows = connection.execute(
                "SELECT COUNT(*) AS correction_count FROM corrections"
            ).fetchone()
            corpus_rows = connection.execute(
                "SELECT COUNT(*) AS corpus_count FROM training_examples WHERE status = 'ready'"
            ).fetchone()
            training_rows = connection.execute(
                "SELECT COUNT(*) AS training_run_count FROM training_runs"
            ).fetchone()
        return {
            "total": totals["total"] or 0,
            "review_count": totals["review_count"] or 0,
            "verified_count": totals["verified_count"] or 0,
            "approved_count": totals["approved_count"] or 0,
            "error_count": totals["error_count"] or 0,
            "ingested_count": totals["ingested_count"] or 0,
            "failed_ingestion_count": totals["failed_ingestion_count"] or 0,
            "avg_confidence": totals["avg_confidence"] or 0,
            "correction_count": correction_rows["correction_count"] or 0,
            "corpus_count": corpus_rows["corpus_count"] or 0,
            "training_run_count": training_rows["training_run_count"] or 0,
        }

    def upsert_training_example(
        self,
        document_id: str,
        po_box: str,
        vendor: str | None,
        status: str,
        strategy_source: str,
        example_dir: str,
        file_path: str,
        json_path: str,
        alignment_path: str | None,
        metadata: dict[str, Any],
    ) -> None:
        now = utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO training_examples (
                  document_id, po_box, vendor, status, strategy_source, example_dir,
                  file_path, json_path, alignment_path, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                  po_box=excluded.po_box,
                  vendor=excluded.vendor,
                  status=excluded.status,
                  strategy_source=excluded.strategy_source,
                  example_dir=excluded.example_dir,
                  file_path=excluded.file_path,
                  json_path=excluded.json_path,
                  alignment_path=excluded.alignment_path,
                  metadata_json=excluded.metadata_json,
                  updated_at=excluded.updated_at
                """,
                (
                    document_id,
                    po_box,
                    vendor,
                    status,
                    strategy_source,
                    example_dir,
                    file_path,
                    json_path,
                    alignment_path,
                    json_dumps(metadata),
                    now,
                    now,
                ),
            )

    def list_training_examples(
        self,
        status: str | None = None,
        po_box: str | None = None,
        vendor: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM training_examples"
        params: list[Any] = []
        clauses: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if po_box:
            clauses.append("po_box = ?")
            params.append(po_box)
        if vendor:
            clauses.append("COALESCE(vendor, '') LIKE ?")
            params.append(f"%{vendor}%")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            dict(row) | {"metadata": json.loads(row["metadata_json"] or "{}")}
            for row in rows
        ]

    def get_training_example(self, document_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM training_examples WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if not row:
            return None
        return dict(row) | {"metadata": json.loads(row["metadata_json"] or "{}")}

    def upsert_training_run(
        self,
        run_id: str,
        name: str,
        strategy_name: str,
        status: str,
        selected_document_ids: list[str],
        example_count: int,
        corpus_path: str | None,
        results: dict[str, Any],
        notes: str = "",
        activated_at: str | None = None,
    ) -> None:
        now = utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO training_runs (
                  id, name, strategy_name, status, selected_document_ids_json, example_count,
                  corpus_path, results_json, notes, created_at, updated_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  strategy_name=excluded.strategy_name,
                  status=excluded.status,
                  selected_document_ids_json=excluded.selected_document_ids_json,
                  example_count=excluded.example_count,
                  corpus_path=excluded.corpus_path,
                  results_json=excluded.results_json,
                  notes=excluded.notes,
                  updated_at=excluded.updated_at,
                  activated_at=excluded.activated_at
                """,
                (
                    run_id,
                    name,
                    strategy_name,
                    status,
                    json_dumps(selected_document_ids),
                    example_count,
                    corpus_path,
                    json_dumps(results),
                    notes,
                    now,
                    now,
                    activated_at,
                ),
            )

    def list_training_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM training_runs ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            dict(row)
            | {
                "selected_document_ids": json.loads(row["selected_document_ids_json"] or "[]"),
                "results": json.loads(row["results_json"] or "{}"),
            }
            for row in rows
        ]

    def get_training_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM training_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return dict(row) | {
            "selected_document_ids": json.loads(row["selected_document_ids_json"] or "[]"),
            "results": json.loads(row["results_json"] or "{}"),
        }

    def record_strategy_activation(
        self,
        strategy_name: str,
        training_run_id: str | None = None,
        notes: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_activations (strategy_name, training_run_id, notes, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (strategy_name, training_run_id, notes, utcnow()),
            )

    def list_strategy_activations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_activations ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _row_to_document(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            **dict(row),
            "extraction": json.loads(row["extraction_json"] or "{}"),
            "verification": json.loads(row["verification_json"] or "{}"),
            "alignment": json.loads(row["alignment_json"] or "{}"),
        }
