from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from .utils import short_uid, utcnow
from .viewer import describe_document_pages, render_page_image, render_page_png


SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass(slots=True)
class SavedBill:
    name: str
    path: Path
    page_numbers: list[int]


@dataclass(slots=True)
class BatchSession:
    batch_id: str
    source_path: Path
    output_dir: Path
    original_filename: str
    total_pages: int
    remaining_pages: list[int]
    saved_outputs: list[SavedBill] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)


@dataclass(slots=True)
class CompletedBatchTransition:
    completed_source_path: Path
    next_batch: BatchSession | None = None


def sanitize_upload_filename(filename: str) -> str:
    basename = Path(filename or "batch.pdf").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return cleaned or "batch.pdf"


def batch_state_root(scan_root: Path) -> Path:
    root = scan_root / "bill_splitter_batches"
    root.mkdir(parents=True, exist_ok=True)
    return root


def batch_state_path(scan_root: Path, batch_id: str) -> Path:
    return batch_state_root(scan_root) / f"{batch_id}.json"


def batch_cache_dir(scan_root: Path, batch_id: str) -> Path:
    path = batch_state_root(scan_root) / batch_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def batch_cache_path(scan_root: Path, batch_id: str) -> Path:
    return batch_state_root(scan_root) / batch_id


def create_batch_session(scan_root: Path, source_path: Path, *, original_filename: str | None = None) -> BatchSession:
    resolved_source = source_path.expanduser().resolve()
    _validate_source_path(resolved_source)
    pages = describe_document_pages(resolved_source)
    total_pages = len(pages)
    if total_pages < 1:
        raise ValueError(f"No pages found in source file: {resolved_source}")
    session = BatchSession(
        batch_id=short_uid("batch"),
        source_path=resolved_source,
        output_dir=resolved_source.with_name(f"{resolved_source.stem}_indbills"),
        original_filename=original_filename or resolved_source.name,
        total_pages=total_pages,
        remaining_pages=list(range(1, total_pages + 1)),
    )
    session.output_dir.mkdir(parents=True, exist_ok=True)
    _cache_batch_page_images(scan_root, session)
    save_batch_session(scan_root, session)
    return session


def load_batch_session(scan_root: Path, batch_id: str) -> BatchSession:
    path = batch_state_path(scan_root, batch_id)
    if not path.exists():
        raise FileNotFoundError(f"Batch session not found: {batch_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BatchSession(
        batch_id=str(payload["batch_id"]),
        source_path=Path(payload["source_path"]).expanduser().resolve(),
        output_dir=Path(payload["output_dir"]).expanduser().resolve(),
        original_filename=str(payload["original_filename"]),
        total_pages=int(payload["total_pages"]),
        remaining_pages=[int(page_number) for page_number in payload["remaining_pages"]],
        saved_outputs=[
            SavedBill(
                name=str(item["name"]),
                path=Path(item["path"]).expanduser().resolve(),
                page_numbers=[int(page_number) for page_number in item["page_numbers"]],
            )
            for item in payload.get("saved_outputs", [])
        ],
        created_at=str(payload.get("created_at") or utcnow()),
    )


def save_batch_session(scan_root: Path, session: BatchSession) -> None:
    path = batch_state_path(scan_root, session.batch_id)
    payload = {
        "batch_id": session.batch_id,
        "source_path": str(session.source_path),
        "output_dir": str(session.output_dir),
        "original_filename": session.original_filename,
        "total_pages": session.total_pages,
        "remaining_pages": session.remaining_pages,
        "saved_outputs": [asdict(output) | {"path": str(output.path)} for output in session.saved_outputs],
        "created_at": session.created_at,
    }
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)


def save_selected_pages_as_bill(scan_root: Path, batch_id: str, page_numbers: str | list[int]) -> SavedBill:
    session = load_batch_session(scan_root, batch_id)
    selected_pages = normalize_selected_pages(session, page_numbers)
    output_path = next_output_path(session)
    _write_pdf_from_pages(session.source_path, selected_pages, output_path)
    output = SavedBill(name=output_path.name, path=output_path, page_numbers=selected_pages)
    session.saved_outputs.append(output)
    selected_set = set(selected_pages)
    session.remaining_pages = [page_number for page_number in session.remaining_pages if page_number not in selected_set]
    save_batch_session(scan_root, session)
    return output


def remove_pages_from_batch(scan_root: Path, batch_id: str, page_numbers: str | list[int]) -> BatchSession:
    session = load_batch_session(scan_root, batch_id)
    selected_pages = normalize_selected_pages(session, page_numbers)
    selected_set = set(selected_pages)
    session.remaining_pages = [page_number for page_number in session.remaining_pages if page_number not in selected_set]
    save_batch_session(scan_root, session)
    return session


def complete_batch_session(scan_root: Path, batch_id: str) -> CompletedBatchTransition:
    session = load_batch_session(scan_root, batch_id)
    if session.remaining_pages:
        raise ValueError("Batch still has remaining pages.")
    next_source = next_batch_source_path(session.source_path)
    completed_source = rename_completed_source(session.source_path)
    delete_batch_session(scan_root, batch_id)
    next_batch = create_batch_session(scan_root, next_source) if next_source else None
    return CompletedBatchTransition(completed_source_path=completed_source, next_batch=next_batch)


def normalize_selected_pages(session: BatchSession, page_numbers: str | list[int]) -> list[int]:
    if isinstance(page_numbers, str):
        tokens = [token.strip() for token in page_numbers.split(",") if token.strip()]
        candidates = [int(token) for token in tokens]
    else:
        candidates = [int(page_number) for page_number in page_numbers]
    selected_set = set(candidates)
    selected_pages = [page_number for page_number in session.remaining_pages if page_number in selected_set]
    if not selected_pages:
        raise ValueError("Select at least one page.")
    return selected_pages


def output_file_for_batch(scan_root: Path, batch_id: str, filename: str) -> Path:
    session = load_batch_session(scan_root, batch_id)
    for output in session.saved_outputs:
        if output.name == filename:
            return output.path
    raise FileNotFoundError(f"Saved bill not found: {filename}")


def cached_page_image_path(scan_root: Path, batch_id: str, page_number: int, size: str) -> Path:
    if size not in {"thumb", "preview"}:
        raise ValueError(f"Unsupported page image size: {size}")
    return batch_cache_dir(scan_root, batch_id) / f"page_{page_number}_{size}.png"


def serialize_saved_output(output: SavedBill) -> dict[str, Any]:
    return {
        "name": output.name,
        "path": str(output.path),
        "page_numbers": output.page_numbers,
    }


def next_output_path(session: BatchSession) -> Path:
    while True:
        token = short_uid("indbill").split("_", 1)[1][:8]
        candidate = session.output_dir / f"{token}_indbill.pdf"
        if not candidate.exists():
            return candidate


def next_batch_source_path(source_path: Path) -> Path | None:
    candidates = [
        candidate
        for candidate in sorted(source_path.parent.iterdir(), key=lambda path: path.name.lower())
        if _is_next_batch_candidate(candidate)
    ]
    for index, candidate in enumerate(candidates):
        if candidate.resolve() == source_path.resolve():
            return candidates[index + 1] if index + 1 < len(candidates) else None
    return None


def rename_completed_source(source_path: Path) -> Path:
    target = source_path.with_name(f"{source_path.stem}_d{source_path.suffix}")
    if target.exists():
        token = short_uid("done").split("_", 1)[1][:8]
        target = source_path.with_name(f"{source_path.stem}_d_{token}{source_path.suffix}")
    source_path.rename(target)
    return target


def delete_batch_session(scan_root: Path, batch_id: str) -> None:
    state_path = batch_state_path(scan_root, batch_id)
    if state_path.exists():
        state_path.unlink()
    cache_path = batch_cache_path(scan_root, batch_id)
    if cache_path.exists():
        shutil.rmtree(cache_path)


def _validate_source_path(source_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"Source file does not exist: {source_path}")
    if source_path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
        raise ValueError(f"Unsupported source type: {source_path.suffix}")


def _is_next_batch_candidate(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES and not path.stem.endswith("_d")


def _write_pdf_from_pages(source_path: Path, page_numbers: list[int], output_path: Path) -> None:
    images: list[Image.Image] = []
    try:
        for page_number in page_numbers:
            images.append(render_page_image(source_path, page_number).convert("RGB"))
        if not images:
            raise ValueError(f"No pages available for output: {output_path}")
        images[0].save(output_path, "PDF", resolution=150.0, save_all=True, append_images=images[1:])
    finally:
        for image in images:
            image.close()


def _cache_batch_page_images(scan_root: Path, session: BatchSession) -> None:
    for page_number in range(1, session.total_pages + 1):
        for size, max_width in (("thumb", 220), ("preview", 900)):
            target_path = cached_page_image_path(scan_root, session.batch_id, page_number, size)
            if target_path.exists():
                continue
            png = render_page_png(session.source_path, page_number, max_width=max_width)
            temp_path = target_path.with_suffix(".tmp")
            temp_path.write_bytes(png)
            temp_path.replace(target_path)
