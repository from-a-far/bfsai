from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import load_settings
from .documents import discover_po_boxes, po_box_layout
from .intake import ScanIntakeService
from .pipeline import PipelineProcessor
from .repository import Repository


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("bfsai.worker")


def iter_scan_roots(watch_root: Path, scan_root: Path) -> list[Path]:
    candidates = [scan_root, watch_root / "scans", watch_root]
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(candidate)
    return roots


def sweep_scans_once(intake: ScanIntakeService, scan_root: Path) -> int:
    count = 0
    for path in sorted(scan_root.iterdir() if scan_root.exists() else []):
        if not path.is_file() or path.name.startswith("."):
            continue
        destination = intake.process_scan(path)
        if destination:
            count += 1
            LOGGER.info("routed scan source=%s destination=%s", path, destination)
    return count


def sweep_client_new_once(processor: PipelineProcessor, watch_root: Path) -> int:
    count = 0
    settings = processor.settings
    for po_box in discover_po_boxes(settings):
        layout = po_box_layout(settings, po_box)
        for path in sorted(layout.new.iterdir()):
            if not path.is_file():
                continue
            document_id = processor.process_file(po_box, path)
            if document_id:
                count += 1
                LOGGER.info("processed po_box=%s document_id=%s", po_box, document_id)
    return count


def main() -> None:
    settings = load_settings()
    repository = Repository(settings.database_path)
    intake = ScanIntakeService(settings, repository)
    processor = PipelineProcessor(settings, repository)
    LOGGER.info("watching scans=%s watch_root=%s every %ss", settings.scan_root, settings.watch_root, settings.poll_seconds)
    while True:
        for scan_root in iter_scan_roots(settings.watch_root, settings.scan_root):
            sweep_scans_once(intake, scan_root)
        sweep_client_new_once(processor, settings.watch_root)
        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    main()
