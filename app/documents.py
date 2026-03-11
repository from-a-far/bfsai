from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass(slots=True)
class FolderLayout:
    root: Path
    new: Path
    review: Path
    processed: Path
    output: Path
    other: Path


def po_box_layout(settings: Settings, po_box: str) -> FolderLayout:
    root = settings.watch_root / po_box
    layout = FolderLayout(
        root=root,
        new=root / "new",
        review=root / "review",
        processed=root / "processed",
        output=root / "output",
        other=root / "other",
    )
    for path in (layout.root, layout.new, layout.review, layout.processed, layout.output, layout.other):
        path.mkdir(parents=True, exist_ok=True)
    return layout


def discover_po_boxes(settings: Settings) -> list[str]:
    candidates = []
    for path in sorted(settings.watch_root.iterdir() if settings.watch_root.exists() else []):
        if path.is_dir() and path.name.isdigit():
            candidates.append(path.name)
    return candidates


def move_to_review(settings: Settings, po_box: str, source_path: Path, document_id: str) -> Path:
    layout = po_box_layout(settings, po_box)
    destination = layout.review / f"{document_id}{source_path.suffix.lower()}"
    shutil.move(str(source_path), destination)
    return destination


def move_to_other(settings: Settings, po_box: str, source_path: Path, document_id: str) -> Path:
    layout = po_box_layout(settings, po_box)
    destination = layout.other / f"{document_id}{source_path.suffix.lower()}"
    shutil.move(str(source_path), destination)
    return destination


def output_json_path(settings: Settings, po_box: str, document_id: str) -> Path:
    return po_box_layout(settings, po_box).output / f"{document_id}.json"


def archive_document_path(settings: Settings, po_box: str, document_id: str, suffix: str) -> Path:
    return settings.database_path.parent / "document_files" / po_box / f"{document_id}{suffix.lower()}"


def retain_document_copy(settings: Settings, po_box: str, document_id: str, source_path: Path) -> Path:
    destination = archive_document_path(settings, po_box, document_id, source_path.suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    return destination


def approve_paths(settings: Settings, po_box: str, document_id: str, current_file_path: Path) -> tuple[Path, Path]:
    layout = po_box_layout(settings, po_box)
    approved_file = layout.processed / f"{document_id}_c{current_file_path.suffix.lower()}"
    approved_json = layout.output / f"{document_id}_c.json"
    return approved_file, approved_json


def resolve_document_file_path(settings: Settings, document: dict) -> Path | None:
    current_file_path = document.get("current_file_path")
    if current_file_path:
        candidate = Path(current_file_path)
        if candidate.exists():
            return candidate

    alignment = document.get("alignment") or {}
    for archived_key in ("archived_file_path", "approved_file_path", "review_file_path"):
        candidate_path = alignment.get(archived_key)
        if not candidate_path:
            continue
        candidate = Path(candidate_path)
        if candidate.exists():
            return candidate

    po_box = document.get("po_box")
    document_id = document.get("id")
    if not po_box or not document_id:
        return None

    layout = po_box_layout(settings, po_box)
    suffixes = []
    original_filename = document.get("original_filename")
    if original_filename:
        suffixes.append(Path(original_filename).suffix.lower())
    if current_file_path:
        suffixes.append(Path(current_file_path).suffix.lower())

    checked: set[Path] = set()
    for suffix in filter(None, suffixes):
        archived_candidate = archive_document_path(settings, po_box, document_id, suffix)
        if archived_candidate not in checked:
            checked.add(archived_candidate)
            if archived_candidate.exists():
                return archived_candidate
        for folder in (layout.review, layout.processed, layout.other, layout.new):
            for candidate in (folder / f"{document_id}{suffix}", folder / f"{document_id}_c{suffix}"):
                if candidate in checked:
                    continue
                checked.add(candidate)
                if candidate.exists():
                    return candidate
    return None
