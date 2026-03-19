from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from .fields import AMOUNT_FIELDS, DATE_FIELDS
from .pdfium_guard import PDFIUM_LOCK
from .utils import as_float


PNG_SCALE = 1.75
MULTILINE_FIELDS = {"remittance_address", "billing_address", "physical_billing_address", "service_address"}


def describe_document_pages(file_path: Path) -> list[dict[str, Any]]:
    if file_path.suffix.lower() == ".pdf":
        with PDFIUM_LOCK:
            document = pdfium.PdfDocument(str(file_path))
            return [
                {
                    "page_number": index + 1,
                    "width": int(document[index].get_width()),
                    "height": int(document[index].get_height()),
                }
                for index in range(len(document))
            ]
    with Image.open(file_path) as image:
        return [{"page_number": 1, "width": image.width, "height": image.height}]


def render_page_image(file_path: Path, page_number: int) -> Image.Image:
    if file_path.suffix.lower() == ".pdf":
        with PDFIUM_LOCK:
            document = pdfium.PdfDocument(str(file_path))
            if page_number < 1 or page_number > len(document):
                raise IndexError(f"Invalid page number: {page_number}")
            return document[page_number - 1].render(scale=PNG_SCALE).to_pil().convert("RGB")
    if page_number != 1:
        raise IndexError(f"Invalid page number: {page_number}")
    with Image.open(file_path) as image:
        return image.convert("RGB")


def render_page_png(file_path: Path, page_number: int, max_width: int | None = None) -> bytes:
    image = render_page_image(file_path, page_number)
    try:
        if max_width and image.width > max_width:
            max_height = max(int(image.height * (max_width / image.width)), 1)
            image.thumbnail((max_width, max_height))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        image.close()


def extract_text_from_box(file_path: Path, page_number: int, bbox: dict[str, float], field_name: str) -> dict[str, Any]:
    image = render_page_image(file_path, page_number)
    try:
        left = max(0, min(image.width, int(round(float(bbox.get("left", 0)) * image.width))))
        top = max(0, min(image.height, int(round(float(bbox.get("top", 0)) * image.height))))
        right = max(left + 1, min(image.width, int(round(float(bbox.get("left", 0) + bbox.get("width", 0)) * image.width))))
        bottom = max(top + 1, min(image.height, int(round(float(bbox.get("top", 0) + bbox.get("height", 0)) * image.height))))
        cropped = image.crop((left, top, right, bottom))
        try:
            text = pytesseract.image_to_string(cropped).strip()
        finally:
            cropped.close()
        value = normalize_extracted_value(field_name, text)
        return {
            "text": text,
            "value": value,
            "page_number": page_number,
            "bbox": {
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top,
            },
            "normalized_bbox": {
                "left": round(left / max(image.width, 1), 4),
                "top": round(top / max(image.height, 1), 4),
                "width": round((right - left) / max(image.width, 1), 4),
                "height": round((bottom - top) / max(image.height, 1), 4),
            },
        }
    finally:
        image.close()


def normalize_extracted_value(field_name: str, text: str) -> str | float:
    multiline = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    cleaned = " ".join(text.split())
    if field_name in AMOUNT_FIELDS:
        amount = as_float(cleaned)
        return amount if amount is not None else cleaned
    if field_name in DATE_FIELDS:
        for token in cleaned.replace("|", " ").split():
            if "/" in token or "-" in token:
                return token.strip(".,;:")
    if field_name in MULTILINE_FIELDS:
        return multiline or cleaned
    return cleaned
