from __future__ import annotations

from pathlib import Path

from .alignment import AlignmentService
from .classifier import classify_document
from .config import Settings
from .documents import (
    SUPPORTED_EXTENSIONS,
    copy_to_document_type,
    move_to_document_type,
    move_to_other,
    move_to_review,
    output_json_path,
    retain_document_copy,
)
from .extractor import Extractor
from .ingestion import RailsIngestionService
from .learning import LearningService
from .repository import Repository
from .schemas import DocumentBundle, IngestionResult
from .utils import compact_excerpt, json_dumps, sha256sum, short_uid
from .verifier import Verifier


class PipelineProcessor:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.extractor = Extractor(settings)
        self.verifier = Verifier(settings)
        self.learning = LearningService(repository)
        self.alignment = AlignmentService()
        self.ingestion = RailsIngestionService(settings, repository)

    def process_file(self, po_box: str, source_path: Path) -> str | None:
        if not source_path.exists() or not source_path.is_file():
            return None

        document_id = short_uid()
        if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            other_path = move_to_other(self.settings, po_box, source_path, document_id)
            self.repository.upsert_document(
                {
                    "id": document_id,
                    "po_box": po_box,
                    "original_filename": source_path.name,
                    "current_file_path": str(other_path),
                    "status": "error",
                    "confidence": 0,
                    "extraction": {},
                    "verification": {},
                    "alignment": {"moved_to": "other"},
                    "ingestion_status": "pending",
                    "error_message": "Unsupported file type",
                }
            )
            return document_id

        review_path = move_to_review(self.settings, po_box, source_path, document_id)
        archived_path = retain_document_copy(self.settings, po_box, document_id, review_path)
        self._analyze_document(
            document_id=document_id,
            po_box=po_box,
            original_filename=source_path.name,
            review_path=review_path,
            output_path=output_json_path(self.settings, po_box, document_id),
            archived_path=archived_path,
        )
        return document_id

    def reprocess_document(
        self,
        document_id: str,
        po_box: str,
        original_filename: str,
        review_path: Path,
        output_path: Path,
        archived_path: Path | None = None,
    ) -> None:
        self._analyze_document(
            document_id=document_id,
            po_box=po_box,
            original_filename=original_filename,
            review_path=review_path,
            output_path=output_path,
            archived_path=archived_path,
        )

    def _analyze_document(
        self,
        document_id: str,
        po_box: str,
        original_filename: str,
        review_path: Path,
        output_path: Path,
        archived_path: Path | None = None,
    ) -> None:
        try:
            retained_file = archived_path or retain_document_copy(self.settings, po_box, document_id, review_path)
            ocr_result = self.extractor.read_text(review_path)
            routing = classify_document(ocr_result.text)
            routed_copy_path = None
            if routing.dtype != "other":
                if routing.duplicate_to_type_folder:
                    routed_copy_path = copy_to_document_type(self.settings, po_box, review_path, document_id, routing.dtype)
                elif not routing.should_extract:
                    routed_copy_path = move_to_document_type(self.settings, po_box, review_path, document_id, routing.dtype)
            elif not routing.should_extract:
                routed_copy_path = move_to_other(self.settings, po_box, review_path, document_id)

            if not routing.should_extract:
                current_path = routed_copy_path or review_path
                metadata = {
                    "document_id": document_id,
                    "po_box": po_box,
                    "status": "routed",
                    "document_type": routing.dtype,
                    "reason": routing.reason,
                    "raw_text_excerpt": compact_excerpt(ocr_result.text),
                }
                output_path.write_text(json_dumps(metadata), encoding="utf-8")
                self.repository.upsert_document(
                    {
                        "id": document_id,
                        "po_box": po_box,
                        "original_filename": original_filename,
                        "current_file_path": str(current_path),
                        "current_json_path": str(output_path),
                        "status": "routed",
                        "confidence": 1.0,
                        "extraction": {"document_type": routing.dtype, "raw_text_excerpt": compact_excerpt(ocr_result.text)},
                        "verification": {"status": "skipped", "score": 1.0, "issues": []},
                        "alignment": {
                            "source_filename": original_filename,
                            "archived_file_path": str(retained_file),
                            "document_type": routing.dtype,
                            "routing_reason": routing.reason,
                            "routed_file_path": str(current_path),
                            "output_json_path": str(output_path),
                        },
                        "ingestion_status": "skipped",
                        "ingestion_attempts": 0,
                        "ingestion_error_message": None,
                    }
                )
                return

            hints = self.learning.build_hints(po_box, ocr_result.text)
            learned_field_candidates = self.alignment.extract_from_profiles(
                hints.get("field_alignment_profiles", []),
                ocr_result,
            )
            if learned_field_candidates:
                hints = {**hints, "learned_field_candidates": learned_field_candidates}
            extraction = self.extractor.extract(po_box, ocr_result.text, hints)
            verification = self.verifier.verify(extraction, hints)
            field_alignments = self.alignment.align_extraction(extraction.model_dump(), ocr_result)
            for field_name, payload in learned_field_candidates.items():
                if field_name not in field_alignments or field_alignments[field_name] is None:
                    field_alignments[field_name] = payload
            alignment = {
                "source_filename": original_filename,
                "review_file_path": str(review_path),
                "archived_file_path": str(retained_file),
                "document_type": routing.dtype,
                "routed_copy_path": str(routed_copy_path) if routed_copy_path else None,
                "routing_reason": routing.reason,
                "output_json_path": str(output_path),
                "page_count": ocr_result.page_count,
                "sha256": sha256sum(review_path),
                "field_alignments": field_alignments,
            }
            bundle = DocumentBundle(
                document_id=document_id,
                po_box=po_box,
                status=verification.status,
                extraction=extraction,
                verification=verification,
                alignment=alignment,
                ingestion=IngestionResult(status="pending"),
            )
            output_path.write_text(json_dumps(bundle.model_dump()), encoding="utf-8")
            self.repository.upsert_document(
                {
                    "id": document_id,
                    "po_box": po_box,
                    "original_filename": original_filename,
                    "current_file_path": str(review_path),
                    "current_json_path": str(output_path),
                    "status": verification.status,
                    "vendor": extraction.vendor,
                    "invoice_number": extraction.invoice_number,
                    "invoice_date": extraction.invoice_date,
                    "subtotal": extraction.subtotal,
                    "tax": extraction.tax,
                    "total": extraction.total if extraction.total is not None else extraction.amount_due,
                    "currency": extraction.currency,
                    "payment_terms": extraction.payment_terms,
                    "confidence": verification.score,
                    "extraction": extraction.model_dump(),
                    "verification": verification.model_dump(),
                    "alignment": alignment,
                    "ingestion_status": "pending",
                    "ingestion_attempts": 0,
                    "ingestion_error_message": None,
                }
            )
        except Exception as error:
            other_path = move_to_other(self.settings, po_box, review_path, document_id)
            self.repository.upsert_document(
                {
                    "id": document_id,
                    "po_box": po_box,
                    "original_filename": original_filename,
                    "current_file_path": str(other_path),
                    "status": "error",
                    "confidence": 0,
                    "extraction": {},
                    "verification": {},
                    "alignment": {
                        "moved_to": "other",
                        "archived_file_path": str(archived_path) if archived_path else None,
                    },
                    "ingestion_status": "pending",
                    "error_message": str(error),
                }
            )
