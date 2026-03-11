from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import load_settings
from .documents import discover_po_boxes, po_box_layout
from .pipeline import PipelineProcessor
from .repository import Repository


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("bfsai.worker")


def sweep_once(processor: PipelineProcessor, watch_root: Path) -> int:
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
    processor = PipelineProcessor(settings, repository)
    LOGGER.info("watching %s every %ss", settings.watch_root, settings.poll_seconds)
    while True:
        sweep_once(processor, settings.watch_root)
        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    main()
