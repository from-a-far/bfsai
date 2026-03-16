from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import pytesseract
from PIL import Image, ImageOps, ImageStat

from .utils import normalize_text
from .viewer import describe_document_pages, render_page_image


DEFAULT_REGISTER_KEYWORDS = (
    "register",
    "invoice register",
    "remittance register",
    "batch register",
    "check register",
    "voucher register",
)
SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
REGISTER_HEADER_TERMS = ("invoice", "account", "vendor", "amount", "date", "total")
BILL_HEADER_TERMS = (
    "invoice",
    "bill",
    "statement",
    "amount due",
    "due date",
    "invoice number",
    "account number",
    "balance due",
    "billing address",
    "service address",
    "payment stub",
)
BILL_CONTINUATION_TERMS = (
    "line item",
    "line items",
    "description",
    "service period",
    "meter",
    "current charges",
    "previous charges",
    "subtotal",
    "tax",
    "total",
    "amount due",
    "usage",
    "continued",
)
CHECK_TERMS = (
    "pay to the order of",
    "authorized signature",
    "void after",
    "check number",
    "check no",
    "check date",
    "dollars",
    "memo",
)
COVER_TERMS = (
    "cover page",
    "cover sheet",
    "separator page",
    "scan separator",
    "batch summary",
)
FIRST_PAGE_TERMS = (
    "bill to",
    "statement date",
    "invoice date",
    "invoice number",
    "invoice #",
    "amount due",
    "due date",
    "balance due",
    "service provided to",
    "customer name",
    "remit to",
)
IDENTITY_STOPWORDS = {
    "account",
    "amount",
    "bill",
    "billing",
    "box",
    "customer",
    "date",
    "due",
    "for",
    "from",
    "invoice",
    "issue",
    "number",
    "page",
    "payment",
    "po",
    "statement",
    "to",
    "total",
}
PAGE_PATTERNS = (
    re.compile(r"\bpage\s*[:#]?\s*(\d{1,2})\s*(?:of|/)\s*(\d{1,3})\b", flags=re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:of|/)\s*(\d{1,3})\b", flags=re.IGNORECASE),
)
LABELED_ID_PATTERNS = (
    re.compile(r"\baccount\s*(?:number|no\.?|#)?\s*[:#-]?\s*([a-z0-9][a-z0-9 .-]{4,})", flags=re.IGNORECASE),
    re.compile(r"\binvoice\s*(?:number|no\.?|#)?\s*[:#-]?\s*([a-z0-9][a-z0-9 .-]{2,})", flags=re.IGNORECASE),
)


@dataclass(slots=True)
class PageAnalysis:
    page_number: int
    excerpt: str
    page_type: str
    classification_reason: str
    can_continue_bill: bool = False
    starts_new_bill: bool = False
    page_index: int | None = None
    page_total: int | None = None
    identity_tokens: list[str] = field(default_factory=list)
    labeled_ids: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SplitOutput:
    bill_index: int
    path: Path
    thumbnail_path: Path
    page_numbers: list[int]


@dataclass(slots=True)
class BatchSplitResult:
    source_path: Path
    output_dir: Path
    register_keywords: list[str]
    register_pages: list[int]
    ignored_pages: list[int]
    page_analyses: list[PageAnalysis]
    outputs: list[SplitOutput]
    notes: list[str] = field(default_factory=list)


OcrTextProvider = Callable[[Image.Image, int], str]


def parse_register_keywords(value: str | None) -> list[str]:
    if not value or not value.strip():
        return list(DEFAULT_REGISTER_KEYWORDS)
    seen: set[str] = set()
    keywords: list[str] = []
    for raw_keyword in re.split(r"[\n,]+", value):
        cleaned = normalize_text(raw_keyword)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            keywords.append(cleaned)
    return keywords or list(DEFAULT_REGISTER_KEYWORDS)


def default_register_keywords_text() -> str:
    return ", ".join(DEFAULT_REGISTER_KEYWORDS)


def sanitize_upload_filename(filename: str) -> str:
    basename = Path(filename or "batch.pdf").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return cleaned or "batch.pdf"


def analyze_register_page(text: str, register_keywords: list[str]) -> tuple[bool, list[str]]:
    normalized = normalize_text(text)
    if not normalized:
        return False, []
    matched_keywords = [keyword for keyword in register_keywords if keyword in normalized]
    header_matches = sum(1 for term in REGISTER_HEADER_TERMS if term in normalized)
    has_register_token = "register" in normalized
    is_register = bool(matched_keywords) and (has_register_token or len(matched_keywords) >= 2 or header_matches >= 3)
    return is_register, matched_keywords


def analyze_page(image: Image.Image, text: str, register_keywords: list[str]) -> PageAnalysis:
    normalized = normalize_text(text)
    page_index, page_total = _extract_page_marker(normalized)
    identity_tokens = _identity_tokens(normalized)
    labeled_ids = _extract_labeled_ids(normalized)
    if _looks_blank(image, normalized):
        return PageAnalysis(
            page_number=0,
            excerpt=_excerpt(text),
            page_type="blank",
            classification_reason="Very low text and nearly blank page image.",
            page_index=page_index,
            page_total=page_total,
            identity_tokens=identity_tokens,
            labeled_ids=labeled_ids,
        )

    is_register, matched_keywords = analyze_register_page(text, register_keywords)
    if is_register:
        return PageAnalysis(
            page_number=0,
            excerpt=_excerpt(text),
            page_type="register",
            classification_reason="Matched register keywords and register-style column headers.",
            page_index=page_index,
            page_total=page_total,
            identity_tokens=identity_tokens,
            labeled_ids=labeled_ids,
            matched_keywords=matched_keywords,
        )

    if _looks_like_check(normalized):
        return PageAnalysis(
            page_number=0,
            excerpt=_excerpt(text),
            page_type="check",
            classification_reason="Matched check/payment document terms.",
            page_index=page_index,
            page_total=page_total,
            identity_tokens=identity_tokens,
            labeled_ids=labeled_ids,
        )

    if _looks_like_cover(normalized):
        return PageAnalysis(
            page_number=0,
            excerpt=_excerpt(text),
            page_type="cover",
            classification_reason="Matched cover/separator page terms.",
            page_index=page_index,
            page_total=page_total,
            identity_tokens=identity_tokens,
            labeled_ids=labeled_ids,
        )

    can_continue_bill = _looks_like_bill_continuation(normalized)
    if _looks_like_bill(normalized):
        starts_new_bill = _looks_like_first_bill_page(normalized, page_index)
        return PageAnalysis(
            page_number=0,
            excerpt=_excerpt(text),
            page_type="bill",
            classification_reason="Matched invoice/bill terms.",
            can_continue_bill=can_continue_bill or page_index not in (None, 1),
            starts_new_bill=starts_new_bill,
            page_index=page_index,
            page_total=page_total,
            identity_tokens=identity_tokens,
            labeled_ids=labeled_ids,
        )

    return PageAnalysis(
        page_number=0,
        excerpt=_excerpt(text),
        page_type="other",
        classification_reason="Did not look like a bill, check, register, or cover page.",
        can_continue_bill=can_continue_bill,
        page_index=page_index,
        page_total=page_total,
        identity_tokens=identity_tokens,
        labeled_ids=labeled_ids,
    )


def split_batch_file(
    source_path: Path,
    register_keywords: list[str] | None = None,
    ocr_text_provider: OcrTextProvider | None = None,
) -> BatchSplitResult:
    resolved_source = source_path.expanduser().resolve()
    if not resolved_source.exists():
        raise FileNotFoundError(f"Source file does not exist: {resolved_source}")
    if resolved_source.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
        raise ValueError(f"Unsupported source type: {resolved_source.suffix}")

    active_keywords = parse_register_keywords(",".join(register_keywords or []))
    page_count = len(describe_document_pages(resolved_source))
    if page_count == 0:
        raise ValueError(f"No pages found in source file: {resolved_source}")

    output_dir = resolved_source.with_name(f"{resolved_source.stem}_split")
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_outputs(output_dir, resolved_source.stem)

    analyses: list[PageAnalysis] = []
    register_pages: list[int] = []
    ocr = ocr_text_provider or _default_ocr_text_provider

    for page_number in range(1, page_count + 1):
        image = render_page_image(resolved_source, page_number)
        try:
            text = ocr(image, page_number)
            analysis = analyze_page(image, text, active_keywords)
        finally:
            image.close()
        analysis.page_number = page_number
        if analysis.page_type == "register":
            register_pages.append(page_number)
        analyses.append(analysis)

    notes: list[str] = []
    page_groups = _build_page_groups(analyses)
    kept_pages = {page_number for group in page_groups for page_number in group}
    ignored_pages = [analysis.page_number for analysis in analyses if analysis.page_number not in kept_pages]
    if not page_groups:
        notes.append("No invoice-like pages were detected, so no split bill PDFs were written.")
    if register_pages:
        notes.append(f"Skipped {len(register_pages)} register page(s).")
    ignored_non_register_pages = [page for page in ignored_pages if page not in register_pages]
    if ignored_non_register_pages:
        notes.append(f"Ignored {len(ignored_non_register_pages)} non-bill page(s) such as checks, covers, blanks, or separators.")

    outputs: list[SplitOutput] = []
    for bill_index, page_numbers in enumerate(page_groups, start=1):
        pdf_path = output_dir / f"{resolved_source.stem}_bill_{bill_index:03d}.pdf"
        thumbnail_path = output_dir / f"{resolved_source.stem}_bill_{bill_index:03d}.png"
        _write_split_output(resolved_source, page_numbers, pdf_path, thumbnail_path)
        outputs.append(
            SplitOutput(
                bill_index=bill_index,
                path=pdf_path,
                thumbnail_path=thumbnail_path,
                page_numbers=page_numbers,
            )
        )

    result = BatchSplitResult(
        source_path=resolved_source,
        output_dir=output_dir,
        register_keywords=active_keywords,
        register_pages=register_pages,
        ignored_pages=ignored_pages,
        page_analyses=analyses,
        outputs=outputs,
        notes=notes,
    )
    _write_manifest(result)
    return result


def _default_ocr_text_provider(image: Image.Image, _: int) -> str:
    prepared = _prepare_for_ocr(image)
    try:
        return pytesseract.image_to_string(prepared, config="--psm 6")
    finally:
        prepared.close()


def _prepare_for_ocr(image: Image.Image) -> Image.Image:
    prepared = ImageOps.grayscale(image)
    if max(prepared.size) > 1800:
        prepared.thumbnail((1800, 1800))
    return prepared


def _build_page_groups(analyses: list[PageAnalysis]) -> list[list[int]]:
    groups: list[list[int]] = []
    current_group: list[int] = []
    current_anchor: PageAnalysis | None = None
    for analysis in analyses:
        if analysis.page_type in {"register", "check", "cover"}:
            if current_group:
                groups.append(current_group)
                current_group = []
                current_anchor = None
            continue
        if analysis.page_type == "blank":
            continue
        if analysis.page_type == "bill":
            if not current_group:
                current_group = [analysis.page_number]
                current_anchor = analysis
                continue
            if _starts_distinct_bill(current_anchor, analysis):
                groups.append(current_group)
                current_group = [analysis.page_number]
                current_anchor = analysis
                continue
            current_group.append(analysis.page_number)
            continue
        if current_group and analysis.can_continue_bill:
            current_group.append(analysis.page_number)
            continue
        if current_group:
            groups.append(current_group)
            current_group = []
            current_anchor = None
    if current_group:
        groups.append(current_group)
    return groups


def _write_split_output(source_path: Path, page_numbers: list[int], pdf_path: Path, thumbnail_path: Path) -> None:
    images: list[Image.Image] = []
    try:
        for page_number in page_numbers:
            images.append(render_page_image(source_path, page_number).convert("RGB"))
        if not images:
            raise ValueError(f"No pages available for output: {pdf_path}")
        images[0].save(pdf_path, "PDF", resolution=150.0, save_all=True, append_images=images[1:])
        thumbnail = images[0].copy()
        thumbnail.thumbnail((360, 360))
        thumbnail.save(thumbnail_path, format="PNG")
        thumbnail.close()
    finally:
        for image in images:
            image.close()


def _write_manifest(result: BatchSplitResult) -> None:
    manifest_path = result.output_dir / "split_manifest.json"
    payload = {
        "source_path": str(result.source_path),
        "output_dir": str(result.output_dir),
        "register_keywords": result.register_keywords,
        "register_pages": result.register_pages,
        "ignored_pages": result.ignored_pages,
        "notes": result.notes,
        "page_analyses": [asdict(page) for page in result.page_analyses],
        "outputs": [
            {
                "bill_index": output.bill_index,
                "path": str(output.path),
                "thumbnail_path": str(output.thumbnail_path),
                "page_numbers": output.page_numbers,
            }
            for output in result.outputs
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _clear_previous_outputs(output_dir: Path, stem: str) -> None:
    for path in output_dir.glob(f"{stem}_bill_*"):
        if path.is_file():
            path.unlink()
    manifest_path = output_dir / "split_manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()


def _excerpt(text: str, limit: int = 140) -> str:
    squashed = re.sub(r"\s+", " ", text).strip()
    return squashed[:limit]


def _looks_blank(image: Image.Image, normalized_text: str) -> bool:
    if normalized_text:
        token_count = len(normalized_text.split())
        if token_count > 2:
            return False
    stat = ImageStat.Stat(ImageOps.grayscale(image))
    mean_value = stat.mean[0]
    stddev_value = stat.stddev[0]
    return mean_value >= 245 and stddev_value <= 10


def _looks_like_bill(normalized: str) -> bool:
    if not normalized:
        return False
    if "invoice register" in normalized or "batch register" in normalized:
        return False
    hits = sum(1 for term in BILL_HEADER_TERMS if term in normalized)
    return hits >= 2 or any(token in normalized for token in ("invoice", "bill", "statement"))


def _looks_like_bill_continuation(normalized: str) -> bool:
    if not normalized:
        return False
    hits = sum(1 for term in BILL_CONTINUATION_TERMS if term in normalized)
    return hits >= 2


def _looks_like_check(normalized: str) -> bool:
    hits = sum(1 for term in CHECK_TERMS if term in normalized)
    return hits >= 2


def _looks_like_cover(normalized: str) -> bool:
    return any(term in normalized for term in COVER_TERMS)


def _looks_like_first_bill_page(normalized: str, page_index: int | None) -> bool:
    if page_index == 1:
        return True
    hits = sum(1 for term in FIRST_PAGE_TERMS if term in normalized)
    return hits >= 2


def _extract_page_marker(normalized: str) -> tuple[int | None, int | None]:
    for pattern in PAGE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        current = int(match.group(1))
        total = int(match.group(2))
        if 1 <= current <= total <= 200:
            return current, total
    return None, None


def _identity_tokens(normalized: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9&]+", normalized)
    filtered: list[str] = []
    for token in tokens:
        if len(filtered) >= 10:
            break
        if token in IDENTITY_STOPWORDS:
            continue
        if len(token) < 3 and not token.isdigit():
            continue
        filtered.append(token)
    return filtered


def _extract_labeled_ids(normalized: str) -> list[str]:
    values: list[str] = []
    for pattern in LABELED_ID_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        candidate = re.sub(r"[^a-z0-9]", "", match.group(1).lower())
        if len(candidate) >= 4:
            values.append(candidate[:24])
    return values


def _starts_distinct_bill(current_anchor: PageAnalysis | None, analysis: PageAnalysis) -> bool:
    if current_anchor is None:
        return True
    if analysis.page_index == 1:
        return True
    if analysis.page_index and analysis.page_index > 1:
        return False
    if not analysis.starts_new_bill:
        return False
    if _shares_document_identity(current_anchor, analysis):
        return False
    return True


def _shares_document_identity(left: PageAnalysis, right: PageAnalysis) -> bool:
    if set(left.labeled_ids) & set(right.labeled_ids):
        return True
    left_tokens = set(left.identity_tokens)
    right_tokens = set(right.identity_tokens)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    minimum = min(len(left_tokens), len(right_tokens))
    return overlap >= max(2, minimum // 2)
