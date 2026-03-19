from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .bill_splitter import (
    cached_page_image_path,
    complete_batch_session,
    create_batch_session,
    load_batch_session,
    output_file_for_batch,
    remove_pages_from_batch,
    save_selected_pages_as_bill,
    sanitize_upload_filename,
)
from .config import Settings, load_settings
from .documents import approve_paths, client_layout, discover_po_boxes, output_json_path, po_box_layout, resolve_document_file_path
from .fields import AMOUNT_FIELDS, FIELD_SPECS, serialize_field_specs
from .ingestion import RailsIngestionService
from .intake import ScanIntakeService
from .learning import LearningService
from .pipeline import PipelineProcessor
from .repository import Repository
from .service_manager import ServiceManager
from .schemas import DocumentBundle, InvoiceExtraction, InvoiceLineItem
from .strategy import StrategyService
from .training import TrainingService
from .utils import as_float, json_dumps, utcnow
from .verifier import Verifier
from .viewer import describe_document_pages, extract_text_from_box, render_page_png


def create_app() -> FastAPI:
    app = FastAPI(title="BFSAI")
    settings = load_settings()
    repository = Repository(settings.database_path)
    templates = Jinja2Templates(directory="app/templates")
    strategy = StrategyService(settings, repository)
    pipeline = PipelineProcessor(settings, repository)
    intake = ScanIntakeService(settings, repository)
    ingestion = RailsIngestionService(settings, repository)
    training = TrainingService(settings, repository, strategy)
    service_manager = ServiceManager(settings, Path(__file__).resolve().parents[1])

    app.state.settings = settings
    app.state.repository = repository
    app.state.learning = LearningService(repository)
    app.state.verifier = Verifier(settings)
    app.state.templates = templates
    app.state.pipeline = pipeline
    app.state.intake = intake
    app.state.ingestion = ingestion
    app.state.strategy = strategy
    app.state.training = training
    app.state.service_manager = service_manager

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    def merge_field_alignments(document: dict[str, Any], field_alignments_json: str | None) -> dict[str, Any]:
        alignment = dict(document.get("alignment") or {})
        field_alignments = dict(alignment.get("field_alignments") or {})
        if field_alignments_json:
            try:
                updates = json.loads(field_alignments_json)
            except json.JSONDecodeError as error:
                raise HTTPException(status_code=400, detail=f"Invalid field_alignments_json: {error}") from error
            if isinstance(updates, dict):
                field_alignments = {}
                for field_name, payload in updates.items():
                    if field_name not in {field.name for field in FIELD_SPECS}:
                        continue
                    field_alignments[field_name] = payload
        alignment["field_alignments"] = field_alignments
        return alignment

    def extraction_value_for_form(extraction: dict[str, Any], field_name: str) -> str:
        value = extraction.get(field_name)
        if value in (None, ""):
            return ""
        if field_name in AMOUNT_FIELDS and isinstance(value, (int, float)):
            return f"{float(value):.2f}"
        return str(value)

    def review_queue_navigation(document_id: str) -> dict[str, Any]:
        queue_ids = repository.review_queue_ids()
        if document_id not in queue_ids:
            return {"previous_id": None, "next_id": None, "position": None, "total": len(queue_ids)}
        index = queue_ids.index(document_id)
        previous_id = queue_ids[index - 1] if index > 0 else None
        next_id = queue_ids[index + 1] if index < len(queue_ids) - 1 else None
        return {"previous_id": previous_id, "next_id": next_id, "position": index + 1, "total": len(queue_ids)}

    def unmatched_directory() -> Path:
        return client_layout(settings, "unresolved").other

    def unmatched_file_path(filename: str) -> Path:
        candidate = (unmatched_directory() / Path(filename).name).resolve()
        if candidate.parent != unmatched_directory().resolve() or not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Unmatched file not found")
        return candidate

    def unmatched_entries(search: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        query = (search or "").strip().lower()
        for path in sorted(unmatched_directory().glob("*.pdf")):
            if query and query not in path.name.lower():
                continue
            try:
                ocr_result = app.state.intake.extractor.read_text(path)
                text = ocr_result.text
                match = app.state.intake.suggest_client(path, text)
            except Exception as error:
                text = ""
                match = None
                error_message = str(error)
            else:
                error_message = ""
            excerpt = " ".join(text.split())[:280] if text else ""
            entries.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "excerpt": excerpt,
                    "suggested_po_box": match.po_box if match else "",
                    "suggested_label": f"{match.client_name} - {match.po_box}" if match else "",
                    "match_alias": match.matched_alias if match else "",
                    "match_score": match.score if match else 0,
                    "error_message": error_message,
                }
            )
            if len(entries) >= limit:
                break
        return entries

    def persist_approved_document(
        document: dict[str, Any],
        extraction_payload: dict[str, Any],
        verification_payload: dict[str, Any],
        review_notes: str,
        event_action: str,
    ) -> None:
        document_id = document["id"]
        current_file = resolve_document_file_path(settings, document)
        if not current_file:
            raise HTTPException(status_code=404, detail="Document file not found on disk")
        current_json_path = document.get("current_json_path")
        current_json = Path(current_json_path) if current_json_path else output_json_path(settings, document["po_box"], document_id)
        current_json.parent.mkdir(parents=True, exist_ok=True)
        if not current_json.exists():
            pending_bundle = DocumentBundle(
                document_id=document_id,
                po_box=document["po_box"],
                status=document["status"],
                extraction=InvoiceExtraction.model_validate(extraction_payload),
                verification=app.state.verifier.verify(
                    InvoiceExtraction.model_validate(extraction_payload),
                    extraction_payload.get("learning_hints", {}),
                ),
                alignment=document["alignment"],
            )
            current_json.write_text(json_dumps(pending_bundle.model_dump()), encoding="utf-8")

        approved_file, approved_json = approve_paths(settings, document["po_box"], document_id, current_file)
        approved_file.parent.mkdir(parents=True, exist_ok=True)
        approved_json.parent.mkdir(parents=True, exist_ok=True)
        archived_file_path = (document.get("alignment") or {}).get("archived_file_path")
        if archived_file_path and Path(archived_file_path) == current_file:
            shutil.copy2(current_file, approved_file)
        else:
            shutil.move(str(current_file), approved_file)
        shutil.move(str(current_json), approved_json)

        bundle = DocumentBundle(
            document_id=document_id,
            po_box=document["po_box"],
            status="approved",
            extraction=InvoiceExtraction.model_validate(extraction_payload),
            verification=app.state.verifier.verify(
                InvoiceExtraction.model_validate(extraction_payload),
                extraction_payload.get("learning_hints", {}),
            ),
            alignment=document["alignment"] | {
                "approved_file_path": str(approved_file),
                "approved_json_path": str(approved_json),
            },
        )
        approved_json.write_text(json_dumps(bundle.model_dump()), encoding="utf-8")

        repository.upsert_document(
            {
                "id": document_id,
                "po_box": document["po_box"],
                "original_filename": document["original_filename"],
                "current_file_path": str(approved_file),
                "current_json_path": str(approved_json),
                "status": "approved",
                "vendor": extraction_payload.get("vendor"),
                "invoice_number": extraction_payload.get("invoice_number"),
                "invoice_date": extraction_payload.get("invoice_date"),
                "subtotal": extraction_payload.get("subtotal"),
                "tax": extraction_payload.get("tax"),
                "total": extraction_payload.get("total") if extraction_payload.get("total") is not None else extraction_payload.get("amount_due"),
                "currency": extraction_payload.get("currency"),
                "payment_terms": extraction_payload.get("payment_terms"),
                "confidence": 1.0,
                "extraction": extraction_payload,
                "verification": verification_payload,
                "alignment": bundle.alignment,
                "review_notes": review_notes,
                "ingestion_status": document.get("ingestion_status", "pending"),
                "ingestion_attempts": document.get("ingestion_attempts", 0),
                "ingestion_error_message": document.get("ingestion_error_message"),
                "last_ingestion_attempt_at": document.get("last_ingestion_attempt_at"),
                "ingested_at": document.get("ingested_at"),
                "confirmed_at": utcnow(),
            }
        )
        approved_document = repository.get_document(document_id)
        if approved_document:
            app.state.training.sync_training_example(approved_document)
        repository.record_review_event(document_id, action=event_action, notes=review_notes, payload={"bypass_training": event_action == "quick_approve"})

    def serialize_batch_session(batch: Any) -> dict[str, Any]:
        pages = [
            {
                "page_number": page_number,
                "thumbnail_url": f"/bill-splitter/batches/{batch.batch_id}/pages/{page_number}.png?size=thumb",
                "preview_url": f"/bill-splitter/batches/{batch.batch_id}/pages/{page_number}.png?size=preview",
            }
            for page_number in batch.remaining_pages
        ]
        outputs = [
            {
                "name": output.name,
                "page_numbers": output.page_numbers,
                "download_url": f"/bill-splitter/batches/{batch.batch_id}/outputs/{output.name}",
            }
            for output in batch.saved_outputs
        ]
        return {
            "batch_id": batch.batch_id,
            "source_path": str(batch.source_path),
            "output_dir": str(batch.output_dir),
            "original_filename": batch.original_filename,
            "total_pages": batch.total_pages,
            "remaining_pages": batch.remaining_pages,
            "remaining_count": len(batch.remaining_pages),
            "saved_outputs": outputs,
            "pages": pages,
        }

    def render_bill_splitter_page(
        request: Request,
        *,
        batch: Any | None = None,
        errors: list[str] | None = None,
        message: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            "bill_splitter.html",
            {
                "request": request,
                "batch": serialize_batch_session(batch) if batch else None,
                "errors": errors or [],
                "message": message,
            },
            status_code=status_code,
        )

    def upload_staging_path(settings: Settings, filename: str) -> Path:
        upload_dir = settings.scan_root / "bill_split_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = sanitize_upload_filename(filename)
        token = re.sub(r"[^a-z0-9]+", "", utcnow().lower())[:14]
        return upload_dir / f"{token}_{safe_name}"

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        status: str | None = Query(default=None),
        po_box: str | None = Query(default=None),
        search: str | None = Query(default=None),
        ingestion_status: str | None = Query(default=None),
    ) -> HTMLResponse:
        stats = repository.dashboard_stats()
        documents = repository.list_documents(
            status=status,
            po_box=po_box,
            search=search,
            ingestion_status=ingestion_status,
            limit=100,
        )
        context = {
            "request": request,
            "stats": stats,
            "documents": documents,
            "filters": {
                "status": status or "",
                "po_box": po_box or "",
                "search": search or "",
                "ingestion_status": ingestion_status or "",
            },
            "config": {
                "scan_root": str(settings.scan_root),
                "watch_root": str(settings.watch_root),
                "database_path": str(settings.database_path),
                "poll_seconds": settings.poll_seconds,
                "model": settings.ollama.model,
                "ollama_base_url": settings.ollama.base_url,
                "active_strategy": app.state.strategy.active_strategy_name(),
                "strategy_profiles": app.state.strategy.list_profiles(),
                "rails_enabled": settings.rails.enabled,
                "rails_endpoint": f"{settings.rails.base_url.rstrip('/')}{settings.rails.endpoint_path}",
                "po_boxes": discover_po_boxes(settings),
            },
        }
        return templates.TemplateResponse("dashboard.html", context)

    @app.get("/unmatched", response_class=HTMLResponse)
    def unmatched_dashboard(
        request: Request,
        search: str | None = Query(default=None),
        message: str = Query(default=""),
    ) -> HTMLResponse:
        clients = app.state.intake.client_lookup.list_clients()
        context = {
            "request": request,
            "entries": unmatched_entries(search=search, limit=200),
            "filters": {"search": search or ""},
            "clients": clients,
            "message": message,
        }
        return templates.TemplateResponse("unmatched.html", context)

    @app.get("/unmatched/files/{filename}")
    def unmatched_file_view(filename: str) -> FileResponse:
        path = unmatched_file_path(filename)
        return FileResponse(
            str(path),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{path.name}"'},
        )

    @app.post("/unmatched/assign")
    def assign_unmatched(
        filename: str = Form(...),
        po_box: str = Form(...),
    ) -> RedirectResponse:
        client_key = po_box.strip()
        if not client_key or not client_key.isdigit():
            raise HTTPException(status_code=400, detail="Select a valid client")
        if not po_box_layout(settings, client_key).root.exists():
            raise HTTPException(status_code=400, detail="Client folder does not exist")
        source_path = unmatched_file_path(filename)
        destination = app.state.intake.process_scan(source_path, forced_client_key=client_key)
        if not destination:
            raise HTTPException(status_code=500, detail="Failed to route unmatched file")
        layout = po_box_layout(settings, client_key)
        if destination.parent == layout.new:
            document_id = app.state.pipeline.process_file(client_key, destination)
            if document_id:
                return RedirectResponse(url=f"/documents/{document_id}", status_code=303)
        return RedirectResponse(
            url=f"/unmatched?message=Assigned+{Path(filename).name}+to+{client_key}",
            status_code=303,
        )

    @app.get("/training", response_class=HTMLResponse)
    def training_dashboard(
        request: Request,
        po_box: str | None = Query(default=None),
        vendor: str | None = Query(default=None),
    ) -> HTMLResponse:
        app.state.training.backfill_from_approved(limit=500)
        examples = repository.list_training_examples(po_box=po_box, vendor=vendor, limit=500)
        runs = repository.list_training_runs(limit=100)
        context = {
            "request": request,
            "examples": examples,
            "runs": runs,
            "filters": {"po_box": po_box or "", "vendor": vendor or ""},
            "strategies": app.state.strategy.list_profiles(),
            "active_strategy": app.state.strategy.active_strategy_name(),
            "activations": repository.list_strategy_activations(limit=25),
        }
        return templates.TemplateResponse("training.html", context)

    @app.post("/training/runs")
    def create_training_run(
        name: str = Form(""),
        strategy_name: str = Form("ppstruct_layoutlm_qwen"),
        selected_document_ids: list[str] = Form(default=[]),
        notes: str = Form(""),
    ) -> RedirectResponse:
        document_ids = [document_id for document_id in selected_document_ids if document_id]
        if not document_ids:
            raise HTTPException(status_code=400, detail="Select at least one approved document")
        run_name = name.strip() or f"Training run {utcnow()}"
        try:
            training_run = app.state.training.create_training_run(
                name=run_name,
                strategy_name=strategy_name,
                document_ids=document_ids,
                notes=notes,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return RedirectResponse(url=f"/training?run_id={training_run['id']}", status_code=303)

    @app.post("/training/runs/{run_id}/activate")
    def activate_training_run(run_id: str) -> RedirectResponse:
        try:
            app.state.training.activate_training_run(run_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return RedirectResponse(url="/training", status_code=303)

    @app.post("/strategies/activate")
    def activate_strategy(strategy_name: str = Form(...)) -> RedirectResponse:
        app.state.strategy.activate_strategy(strategy_name, notes="Manual activation from admin UI")
        return RedirectResponse(url="/training", status_code=303)

    @app.get("/services", response_class=HTMLResponse)
    def services_dashboard(request: Request) -> HTMLResponse:
        context = {
            "request": request,
            "services": app.state.service_manager.status(),
            "strategy_status": app.state.strategy.status(),
            "script_paths": {
                "manage_services": str(Path(__file__).resolve().parents[1] / "scripts" / "manage_services.py"),
                "switch_strategy": str(Path(__file__).resolve().parents[1] / "scripts" / "switch_strategy.py"),
            },
        }
        return templates.TemplateResponse("services.html", context)

    @app.post("/services/{service_name}/{action}")
    def service_action(service_name: str, action: str) -> RedirectResponse:
        if service_name not in {"api", "worker", "all"}:
            raise HTTPException(status_code=400, detail="Unknown service")
        if action not in {"start", "stop", "restart"}:
            raise HTTPException(status_code=400, detail="Unknown action")
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "manage_services.py"
        subprocess.Popen(
            [sys.executable, str(script_path), action, service_name],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return RedirectResponse(url="/services", status_code=303)

    @app.get("/bill-splitter", response_class=HTMLResponse)
    def bill_splitter_page(request: Request, message: str = Query(default="")) -> HTMLResponse:
        return render_bill_splitter_page(request, message=message)

    @app.post("/bill-splitter", response_class=HTMLResponse)
    async def bill_splitter_upload(
        request: Request,
        batch_path: str = Form(""),
        file: UploadFile | None = File(default=None),
    ) -> HTMLResponse:
        opened_path = batch_path.strip()
        if opened_path:
            try:
                source_path = Path(opened_path).expanduser().resolve()
                message = "Loaded the batch from disk. Saved bills will be written beside the source batch file."
                batch = create_batch_session(settings.scan_root, source_path, original_filename=source_path.name)
            except Exception as error:
                return render_bill_splitter_page(
                    request,
                    errors=[f"{opened_path}: {error}"],
                    status_code=400,
                )
            finally:
                if file is not None:
                    await file.close()
            return render_bill_splitter_page(request, batch=batch, message=message)

        if file is None or not file.filename:
            return render_bill_splitter_page(
                request,
                errors=["Enter a batch file path or select a batch PDF or scan image to open."],
                status_code=400,
            )

        try:
            original_name = Path(file.filename or "batch.pdf").name
            staged_name = sanitize_upload_filename(original_name)
            staged_path = upload_staging_path(settings, staged_name)
            staged_path.write_bytes(await file.read())
            source_path = staged_path
            message = "Loaded the uploaded batch copy. Saved bills will be written beside the staged batch file."
            batch = create_batch_session(settings.scan_root, source_path, original_filename=original_name)
        except Exception as error:
            return render_bill_splitter_page(
                request,
                errors=[f"{file.filename or 'batch'}: {error}"],
                status_code=400,
            )
        finally:
            await file.close()

        return render_bill_splitter_page(request, batch=batch, message=message)

    @app.get("/bill-splitter/batches/{batch_id}", response_class=HTMLResponse)
    def bill_splitter_batch_page(
        request: Request,
        batch_id: str,
        message: str = Query(default=""),
    ) -> HTMLResponse:
        try:
            batch = load_batch_session(settings.scan_root, batch_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return render_bill_splitter_page(request, batch=batch, message=message)

    @app.get("/bill-splitter/batches/{batch_id}/pages/{page_number}.png")
    def bill_splitter_page_image(batch_id: str, page_number: int, size: str = Query(default="thumb")) -> Response:
        try:
            batch = load_batch_session(settings.scan_root, batch_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if page_number < 1 or page_number > batch.total_pages:
            raise HTTPException(status_code=404, detail="Page not found")
        try:
            image_path = cached_page_image_path(settings.scan_root, batch_id, page_number, size)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not image_path.exists():
            raise HTTPException(status_code=404, detail="Page image not found")
        try:
            return FileResponse(str(image_path), media_type="image/png", filename=image_path.name)
        except IndexError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/bill-splitter/batches/{batch_id}/save")
    def bill_splitter_save(batch_id: str, page_numbers: str = Form("")) -> RedirectResponse:
        try:
            output = save_selected_pages_as_bill(settings.scan_root, batch_id, page_numbers)
            batch = load_batch_session(settings.scan_root, batch_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not batch.remaining_pages:
            transition = complete_batch_session(settings.scan_root, batch_id)
            completed_name = transition.completed_source_path.name
            if transition.next_batch:
                return RedirectResponse(
                    url=f"/bill-splitter/batches/{transition.next_batch.batch_id}?message=Saved+{output.name}+and+completed+{completed_name}",
                    status_code=303,
                )
            return RedirectResponse(
                url=f"/bill-splitter?message=Saved+{output.name}+and+completed+{completed_name}",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/bill-splitter/batches/{batch_id}?message=Saved+{output.name}",
            status_code=303,
        )

    @app.post("/bill-splitter/batches/{batch_id}/remove")
    def bill_splitter_remove(batch_id: str, page_numbers: str = Form("")) -> RedirectResponse:
        try:
            batch = remove_pages_from_batch(settings.scan_root, batch_id, page_numbers)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        removed_count = len(page_numbers.split(",")) if page_numbers.strip() else 0
        if not batch.remaining_pages:
            transition = complete_batch_session(settings.scan_root, batch_id)
            completed_name = transition.completed_source_path.name
            if transition.next_batch:
                return RedirectResponse(
                    url=f"/bill-splitter/batches/{transition.next_batch.batch_id}?message=Removed+{removed_count}+page(s)+and+completed+{completed_name}",
                    status_code=303,
                )
            return RedirectResponse(
                url=f"/bill-splitter?message=Removed+{removed_count}+page(s)+and+completed+{completed_name}",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/bill-splitter/batches/{batch.batch_id}?message=Removed+{removed_count}+page(s)+from+the+batch",
            status_code=303,
        )

    @app.get("/bill-splitter/batches/{batch_id}/outputs/{filename}")
    def bill_splitter_output(batch_id: str, filename: str) -> FileResponse:
        try:
            output_path = output_file_for_batch(settings.scan_root, batch_id, filename)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return FileResponse(str(output_path), media_type="application/pdf", filename=output_path.name)

    @app.get("/documents/{document_id}", response_class=HTMLResponse)
    def document_detail(request: Request, document_id: str) -> HTMLResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        resolved_file_path = resolve_document_file_path(settings, document)
        document_pages = []
        if resolved_file_path:
            for page in describe_document_pages(resolved_file_path):
                document_pages.append(page | {"image_url": f"/documents/{document_id}/pages/{page['page_number']}.png"})
        return templates.TemplateResponse(
            "document.html",
            {
                "request": request,
                "document": document,
                "resolved_file_path": str(resolved_file_path) if resolved_file_path else None,
                "line_items_json": json_dumps(document["extraction"].get("line_items", [])),
                "extraction_json": json_dumps(document["extraction"]),
                "verification_json": json_dumps(document["verification"]),
                "review_events": repository.list_review_events(document_id),
                "ingestion_events": repository.list_ingestion_events(document_id),
                "document_pages_json": json_dumps(document_pages),
                "field_specs_json": json_dumps(serialize_field_specs()),
                "field_alignments_json": json_dumps(document["alignment"].get("field_alignments", {})),
                "field_specs": FIELD_SPECS,
                "extraction_value_for_form": extraction_value_for_form,
                "queue_navigation": review_queue_navigation(document_id),
                "field_alignments": document["alignment"].get("field_alignments", {}),
            },
        )

    @app.get("/api/documents/{document_id}")
    def document_json(document_id: str) -> JSONResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return JSONResponse(document)

    @app.get("/files/{document_id}")
    def file_view(document_id: str) -> FileResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        resolved_file_path = resolve_document_file_path(settings, document)
        if not resolved_file_path:
            raise HTTPException(status_code=404, detail="Document file not found on disk")
        return FileResponse(str(resolved_file_path))

    @app.get("/documents/{document_id}/pages/{page_number}.png")
    def document_page_image(
        document_id: str,
        page_number: int,
        max_width: int = Query(default=1100, ge=200, le=1600),
    ) -> Response:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        resolved_file_path = resolve_document_file_path(settings, document)
        if not resolved_file_path:
            raise HTTPException(status_code=404, detail="Document file not found on disk")
        try:
            png = render_page_png(resolved_file_path, page_number, max_width=max_width)
        except IndexError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(content=png, media_type="image/png")

    @app.post("/api/documents/{document_id}/extract-box")
    async def extract_box(document_id: str, request: Request) -> JSONResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        resolved_file_path = resolve_document_file_path(settings, document)
        if not resolved_file_path:
            raise HTTPException(status_code=404, detail="Document file not found on disk")
        payload = await request.json()
        field_name = str(payload.get("field_name") or "")
        bbox = payload.get("bbox") or {}
        page_number = int(payload.get("page_number") or 1)
        if field_name not in {field.name for field in FIELD_SPECS}:
            raise HTTPException(status_code=400, detail="Unknown field")
        result = extract_text_from_box(resolved_file_path, page_number, bbox, field_name)
        return JSONResponse(result)

    @app.post("/documents/{document_id}/note")
    def save_note(
        document_id: str,
        review_notes: str = Form(""),
    ) -> RedirectResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        repository.upsert_document(
            {
                **document,
                "extraction": document["extraction"],
                "verification": document["verification"],
                "alignment": document["alignment"],
                "review_notes": review_notes,
            }
        )
        repository.record_review_event(document_id, action="note", notes=review_notes)
        return RedirectResponse(url=f"/documents/{document_id}", status_code=303)

    @app.post("/documents/{document_id}/reprocess")
    def reprocess_document(
        document_id: str,
        review_notes: str = Form(""),
    ) -> RedirectResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        if document["status"] == "approved":
            raise HTTPException(status_code=400, detail="Approved documents should not be reprocessed in place")
        resolved_file_path = resolve_document_file_path(settings, document)
        if not resolved_file_path:
            raise HTTPException(status_code=404, detail="Document file not found on disk")
        target_json_path = (
            Path(document["current_json_path"])
            if document.get("current_json_path")
            else output_json_path(settings, document["po_box"], document_id)
        )
        pipeline.reprocess_document(
            document_id=document_id,
            po_box=document["po_box"],
            original_filename=document["original_filename"],
            review_path=resolved_file_path,
            output_path=target_json_path,
        )
        updated = repository.get_document(document_id)
        repository.upsert_document(
            {
                **updated,
                "extraction": updated["extraction"],
                "verification": updated["verification"],
                "alignment": updated["alignment"],
                "review_notes": review_notes or document.get("review_notes"),
            }
        )
        repository.record_review_event(document_id, action="reprocess", notes=review_notes)
        return RedirectResponse(url=f"/documents/{document_id}", status_code=303)

    @app.post("/documents/{document_id}/ingest")
    def ingest_document(document_id: str) -> RedirectResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        if document["status"] != "approved":
            raise HTTPException(status_code=400, detail="Only approved documents can be ingested")
        result = ingestion.ingest_document(document)
        repository.record_review_event(document_id, action="ingest", notes=result.get("status", "unknown"), payload=result)
        return RedirectResponse(url=f"/documents/{document_id}", status_code=303)

    @app.post("/documents/{document_id}/quick-approve")
    def quick_approve_document(
        document_id: str,
        review_notes: str = Form(""),
    ) -> RedirectResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        queue_navigation = review_queue_navigation(document_id)
        extraction_payload = dict(document["extraction"])
        extraction_payload["po_box"] = document["po_box"]
        extraction_payload["confidence"] = max(float(document.get("confidence") or 0), 0.99)
        verification = app.state.verifier.verify(
            InvoiceExtraction.model_validate(extraction_payload),
            extraction_payload.get("learning_hints", {}),
        )
        persist_approved_document(
            document=document,
            extraction_payload=extraction_payload,
            verification_payload=verification.model_dump() | {"bypass_training": True},
            review_notes=review_notes,
            event_action="quick_approve",
        )
        redirect_url = f"/documents/{queue_navigation['next_id']}" if queue_navigation["next_id"] else "/"
        return RedirectResponse(url=redirect_url, status_code=303)

    @app.post("/documents/{document_id}/delete")
    def delete_document(document_id: str) -> RedirectResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        for candidate in (document.get("current_file_path"), document.get("current_json_path")):
            if not candidate:
                continue
            path = Path(candidate)
            if path.exists():
                path.unlink()
        for candidate in (
            (document.get("alignment") or {}).get("archived_file_path"),
            (document.get("alignment") or {}).get("approved_file_path"),
            (document.get("alignment") or {}).get("routed_copy_path"),
            (document.get("alignment") or {}).get("routed_file_path"),
        ):
            if not candidate:
                continue
            path = Path(candidate)
            if path.exists():
                path.unlink()
        repository.delete_document(document_id)
        return RedirectResponse(url="/", status_code=303)

    @app.post("/documents/{document_id}/approve")
    def approve_document(
        document_id: str,
        vendor: str = Form(""),
        payable_to: str = Form(""),
        remittance_address: str = Form(""),
        billing_address: str = Form(""),
        physical_billing_address: str = Form(""),
        service_address: str = Form(""),
        account_number: str = Form(""),
        friendly_name: str = Form(""),
        name_on_account: str = Form(""),
        invoice_number: str = Form(""),
        invoice_date: str = Form(""),
        due_date: str = Form(""),
        subtotal: str = Form(""),
        tax: str = Form(""),
        total: str = Form(""),
        amount_due: str = Form(""),
        currency: str = Form("USD"),
        payment_terms: str = Form(""),
        previous_amount_due: str = Form(""),
        previous_payment_date: str = Form(""),
        previous_payment_amount: str = Form(""),
        line_items_json: str = Form("[]"),
        field_alignments_json: str = Form("{}"),
        review_notes: str = Form(""),
    ) -> RedirectResponse:
        document = repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        queue_navigation = review_queue_navigation(document_id)

        try:
            line_items_payload = json.loads(line_items_json or "[]")
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=400, detail=f"Invalid line_items_json: {error}") from error

        corrected_extraction = InvoiceExtraction(
            vendor=vendor or None,
            payable_to=payable_to or None,
            remittance_address=remittance_address or None,
            billing_address=billing_address or None,
            physical_billing_address=physical_billing_address or None,
            service_address=service_address or None,
            account_number=account_number or None,
            friendly_name=friendly_name or None,
            name_on_account=name_on_account or None,
            invoice_number=invoice_number or None,
            invoice_date=invoice_date or None,
            due_date=due_date or None,
            subtotal=as_float(subtotal),
            tax=as_float(tax),
            total=as_float(total),
            amount_due=as_float(amount_due),
            currency=currency or "USD",
            payment_terms=payment_terms or None,
            previous_amount_due=as_float(previous_amount_due),
            previous_payment_date=previous_payment_date or None,
            previous_payment_amount=as_float(previous_payment_amount),
            po_box=document["po_box"],
            line_items=[
                InvoiceLineItem(
                    description=str(item.get("description", "")),
                    quantity=as_float(item.get("quantity")),
                    unit_price=as_float(item.get("unit_price")),
                    amount=as_float(item.get("amount")),
                )
                for item in line_items_payload
                if item.get("description")
            ],
            confidence=max(float(document["confidence"]), 0.99),
            raw_text_excerpt=document["extraction"].get("raw_text_excerpt"),
            model_source=f'{document["extraction"].get("model_source", "heuristic")}+confirmed',
            learning_hints=document["extraction"].get("learning_hints", {}),
        )
        document["alignment"] = merge_field_alignments(document, field_alignments_json)
        correction_count = app.state.learning.record_confirmation(
            document,
            corrected_extraction.model_dump(),
            field_alignments=document["alignment"].get("field_alignments", {}),
        )
        verification = app.state.verifier.verify(corrected_extraction, corrected_extraction.learning_hints)

        persist_approved_document(
            document=document,
            extraction_payload=corrected_extraction.model_dump(),
            verification_payload=verification.model_dump() | {"correction_count": correction_count},
            review_notes=review_notes,
            event_action="approve",
        )
        refreshed = repository.get_document(document_id)
        result = ingestion.ingest_document(refreshed)
        repository.record_review_event(document_id, action="auto_ingest", notes=result.get("status", "unknown"), payload=result)
        redirect_url = f"/documents/{queue_navigation['next_id']}" if queue_navigation["next_id"] else "/"
        return RedirectResponse(url=redirect_url, status_code=303)

    return app
