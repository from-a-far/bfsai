from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import Settings
from .documents import resolve_document_file_path
from .extractor import Extractor
from .fields import AMOUNT_FIELDS, DATE_FIELDS, TRACKED_FIELDS
from .learning import LearningService
from .repository import Repository
from .strategy import StrategyService
from .utils import json_dumps, normalize_text, short_uid, utcnow, as_float


class TrainingService:
    def __init__(self, settings: Settings, repository: Repository, strategy: StrategyService):
        self.settings = settings
        self.repository = repository
        self.strategy = strategy

    def sync_training_example(self, document: dict[str, Any]) -> dict[str, Any] | None:
        if document.get("status") != "approved":
            return None
        source_path = resolve_document_file_path(self.settings, document)
        json_path = document.get("current_json_path")
        if not source_path or not json_path:
            return None
        example_dir = self.settings.extraction.corpus_dir / "examples" / document["id"]
        example_dir.mkdir(parents=True, exist_ok=True)
        copied_file = example_dir / Path(str(source_path)).name
        copied_json = example_dir / Path(str(json_path)).name
        shutil.copy2(source_path, copied_file)
        shutil.copy2(json_path, copied_json)
        alignment_path = example_dir / "alignment.json"
        ground_truth_path = example_dir / "ground_truth.json"
        manifest_path = example_dir / "manifest.json"
        alignment_payload = document.get("alignment") or {}
        extraction_payload = document.get("extraction") or {}
        alignment_path.write_text(json_dumps(alignment_payload), encoding="utf-8")
        ground_truth_path.write_text(json_dumps(extraction_payload), encoding="utf-8")
        manifest = {
            "document_id": document["id"],
            "po_box": document["po_box"],
            "vendor": document.get("vendor"),
            "approved_file_path": str(copied_file),
            "approved_json_path": str(copied_json),
            "alignment_path": str(alignment_path),
            "ground_truth_path": str(ground_truth_path),
            "strategy_source": extraction_payload.get("model_source"),
            "updated_at": utcnow(),
        }
        manifest_path.write_text(json_dumps(manifest), encoding="utf-8")
        self.repository.upsert_training_example(
            document_id=document["id"],
            po_box=document["po_box"],
            vendor=document.get("vendor"),
            status="ready",
            strategy_source=str(extraction_payload.get("model_source") or "unknown"),
            example_dir=str(example_dir),
            file_path=str(copied_file),
            json_path=str(copied_json),
            alignment_path=str(alignment_path),
            metadata=manifest,
        )
        return self.repository.get_training_example(document["id"])

    def ensure_training_examples(self, document_ids: list[str]) -> list[dict[str, Any]]:
        examples: list[dict[str, Any]] = []
        for document in self.repository.list_documents_by_ids(document_ids):
            example = self.sync_training_example(document)
            if example:
                examples.append(example)
        return examples

    def backfill_from_approved(self, limit: int = 250) -> list[dict[str, Any]]:
        examples: list[dict[str, Any]] = []
        for document in self.repository.list_documents(status="approved", limit=limit):
            example = self.sync_training_example(document)
            if example:
                examples.append(example)
        return examples

    def create_training_run(
        self,
        name: str,
        strategy_name: str,
        document_ids: list[str],
        notes: str = "",
    ) -> dict[str, Any]:
        examples = self.ensure_training_examples(document_ids)
        if not examples:
            raise ValueError("No approved examples were available for training")
        run_id = short_uid("train")
        run_dir = self.settings.extraction.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        corpus_manifest = self._write_training_manifests(run_dir, strategy_name, examples)
        baseline_name = self.strategy.active_strategy_name()
        baseline_results = self.evaluate_documents(document_ids, baseline_name)
        candidate_results = self.evaluate_documents(document_ids, strategy_name)
        results = {
            "baseline_strategy": baseline_name,
            "candidate_strategy": strategy_name,
            "baseline": baseline_results,
            "candidate": candidate_results,
            "improved": float(candidate_results.get("average_score") or 0) > float(baseline_results.get("average_score") or 0),
        }
        self.repository.upsert_training_run(
            run_id=run_id,
            name=name,
            strategy_name=strategy_name,
            status="completed",
            selected_document_ids=document_ids,
            example_count=len(examples),
            corpus_path=str(corpus_manifest),
            results=results,
            notes=notes,
        )
        return self.repository.get_training_run(run_id) or {}

    def activate_training_run(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_training_run(run_id)
        if not run:
            raise ValueError("Training run not found")
        self.strategy.activate_strategy(
            run["strategy_name"],
            training_run_id=run_id,
            notes=f"Activated from training run {run_id}",
        )
        self.repository.upsert_training_run(
            run_id=run["id"],
            name=run["name"],
            strategy_name=run["strategy_name"],
            status="active",
            selected_document_ids=run["selected_document_ids"],
            example_count=run["example_count"],
            corpus_path=run.get("corpus_path"),
            results=run["results"],
            notes=run.get("notes") or "",
            activated_at=utcnow(),
        )
        return self.repository.get_training_run(run_id) or run

    def evaluate_documents(self, document_ids: list[str], strategy_name: str) -> dict[str, Any]:
        documents = self.repository.list_documents_by_ids(document_ids)
        results: list[dict[str, Any]] = []
        for document in documents:
            source_path = resolve_document_file_path(self.settings, document)
            if not source_path:
                continue
            extracted = self._extract_for_strategy(source_path, document["po_box"], strategy_name)
            score = self._score_extraction(extracted, document.get("extraction") or {})
            results.append(
                {
                    "document_id": document["id"],
                    "vendor": document.get("vendor"),
                    "score": score,
                    "model_source": extracted.get("model_source"),
                }
            )
        average_score = round(sum(item["score"] for item in results) / max(len(results), 1), 4) if results else 0.0
        return {
            "document_count": len(results),
            "average_score": average_score,
            "documents": results,
        }

    def _write_training_manifests(
        self,
        run_dir: Path,
        strategy_name: str,
        examples: list[dict[str, Any]],
    ) -> Path:
        manifest = {
            "strategy_name": strategy_name,
            "created_at": utcnow(),
            "examples": [example["metadata"] for example in examples],
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json_dumps(manifest), encoding="utf-8")

        qwen_jsonl_path = run_dir / "qwen_train.jsonl"
        layout_jsonl_path = run_dir / "layoutlm_annotations.jsonl"
        with qwen_jsonl_path.open("w", encoding="utf-8") as qwen_handle, layout_jsonl_path.open("w", encoding="utf-8") as layout_handle:
            for example in examples:
                metadata = example["metadata"]
                ground_truth = json.loads(Path(metadata["ground_truth_path"]).read_text(encoding="utf-8"))
                qwen_record = {
                    "document_id": example["document_id"],
                    "messages": [
                        {"role": "system", "content": "Extract invoice fields as JSON."},
                        {"role": "user", "content": f"Document path: {metadata['approved_file_path']}"},
                        {"role": "assistant", "content": json.dumps(ground_truth, default=str)},
                    ],
                }
                layout_record = {
                    "document_id": example["document_id"],
                    "document_path": metadata["approved_file_path"],
                    "alignment_path": metadata["alignment_path"],
                    "ground_truth_path": metadata["ground_truth_path"],
                }
                qwen_handle.write(json.dumps(qwen_record, default=str) + "\n")
                layout_handle.write(json.dumps(layout_record, default=str) + "\n")
        return manifest_path

    def _extract_for_strategy(self, file_path: Path, po_box: str, strategy_name: str) -> dict[str, Any]:
        extraction_settings = replace(self.settings.extraction, active_strategy=strategy_name)
        strategy_settings = replace(self.settings, extraction=extraction_settings)
        extractor = Extractor(strategy_settings)
        learning = LearningService(self.repository)
        ocr_result = extractor.read_text(file_path)
        hints = learning.build_hints(po_box, ocr_result.text)
        extraction = extractor.extract(po_box, ocr_result.text, hints)
        return extraction.model_dump()

    def _score_extraction(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> float:
        scores: list[float] = []
        for field_name in TRACKED_FIELDS:
            scores.append(self._field_score(field_name, extracted.get(field_name), ground_truth.get(field_name)))
        return round(sum(scores) / max(len(scores), 1), 4)

    def _field_score(self, field_name: str, left: Any, right: Any) -> float:
        if left in (None, "") and right in (None, ""):
            return 1.0
        if field_name in AMOUNT_FIELDS:
            left_amount = as_float(left)
            right_amount = as_float(right)
            if left_amount is None or right_amount is None:
                return 0.0
            return 1.0 if abs(left_amount - right_amount) <= 0.01 else 0.0
        if field_name in DATE_FIELDS:
            return 1.0 if normalize_text(str(left)) == normalize_text(str(right)) else 0.0
        return 1.0 if normalize_text(str(left)) == normalize_text(str(right)) else 0.0
