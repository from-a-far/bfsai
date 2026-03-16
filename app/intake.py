from __future__ import annotations

from pathlib import Path

from .classifier import classify_document
from .config import Settings
from .documents import (
    SUPPORTED_EXTENSIONS,
    copy_client_new_to_document_type,
    known_client_layout,
    move_scan_to_client_document_type,
    move_scan_to_client_new,
    move_scan_to_client_other,
)
from .extractor import Extractor
from .repository import Repository
from .utils import detect_po_box


UNRESOLVED_CLIENT_KEY = "unresolved"


class ScanIntakeService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.extractor = Extractor(settings)

    def process_scan(self, source_path: Path) -> Path | None:
        if not source_path.exists() or not source_path.is_file():
            return None

        client_key = UNRESOLVED_CLIENT_KEY
        if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return move_scan_to_client_other(self.settings, client_key, source_path)

        try:
            ocr_result = self.extractor.read_text(source_path)
        except Exception:
            return move_scan_to_client_other(self.settings, client_key, source_path)
        detected_po_box = detect_po_box(ocr_result.text)
        if detected_po_box and known_client_layout(self.settings, detected_po_box):
            client_key = detected_po_box

        routing = classify_document(ocr_result.text)
        if routing.should_extract:
            new_path = move_scan_to_client_new(self.settings, client_key, source_path)
            if routing.duplicate_to_type_folder:
                copy_client_new_to_document_type(self.settings, client_key, new_path, routing.dtype)
            return new_path
        if routing.dtype != "other":
            return move_scan_to_client_document_type(self.settings, client_key, source_path, routing.dtype)
        return move_scan_to_client_other(self.settings, client_key, source_path)
