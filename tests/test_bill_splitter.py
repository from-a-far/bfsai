from pathlib import Path

from PIL import Image

from app.bill_splitter import parse_register_keywords, split_batch_file
from app.viewer import describe_document_pages


def make_batch_pdf(path: Path, page_count: int) -> None:
    pages = [Image.new("RGB", (220, 320), color=(index * 30 % 255, 120, 180)) for index in range(page_count)]
    try:
        pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])
    finally:
        for page in pages:
            page.close()


def test_parse_register_keywords_deduplicates_and_normalizes() -> None:
    keywords = parse_register_keywords(" Register , invoice register\nregister ")

    assert keywords == ["register", "invoice register"]


def test_split_batch_file_creates_one_pdf_per_register_delimited_bill(tmp_path: Path) -> None:
    source_path = tmp_path / "batch.pdf"
    make_batch_pdf(source_path, 5)

    texts = {
        1: "Invoice Register Vendor Amount Date Total",
        2: "Acme Utility invoice 123",
        3: "continued line items",
        4: "Batch Register invoice amount date total",
        5: "Beta Water invoice 999",
    }

    result = split_batch_file(
        source_path,
        register_keywords=["register", "invoice register", "batch register"],
        ocr_text_provider=lambda _image, page_number: texts[page_number],
    )

    assert result.register_pages == [1, 4]
    assert [output.page_numbers for output in result.outputs] == [[2, 3], [5]]
    assert all(output.path.exists() for output in result.outputs)
    assert all(output.thumbnail_path.exists() for output in result.outputs)
    assert len(describe_document_pages(result.outputs[0].path)) == 2
    assert len(describe_document_pages(result.outputs[1].path)) == 1
    assert (result.output_dir / "split_manifest.json").exists()
    assert result.ignored_pages == [1, 4]


def test_split_batch_file_ignores_checks_and_blank_pages(tmp_path: Path) -> None:
    source_path = tmp_path / "mixed.pdf"
    make_batch_pdf(source_path, 6)

    result = split_batch_file(
        source_path,
        register_keywords=["register"],
        ocr_text_provider=lambda _image, page_number: {
            1: "Invoice Register Vendor Amount Date Total",
            2: "Invoice number 12345 amount due 42.10 due date 03/30/2026",
            3: "Line items current charges subtotal tax total",
            4: "Pay to the order of ACME dollars authorized signature check number 1001",
            5: "",
            6: "Statement account number 1111 balance due 77.50",
        }[page_number],
    )

    assert result.register_pages == [1]
    assert result.ignored_pages == [1, 4, 5]
    assert [output.page_numbers for output in result.outputs] == [[2, 3], [6]]


def test_split_batch_file_returns_no_outputs_when_no_bills_are_detected(tmp_path: Path) -> None:
    source_path = tmp_path / "non_bills.pdf"
    make_batch_pdf(source_path, 3)

    result = split_batch_file(
        source_path,
        register_keywords=["register"],
        ocr_text_provider=lambda _image, page_number: {
            1: "Register invoice amount date total",
            2: "Pay to the order of ACME dollars authorized signature check number 1002",
            3: "",
        }[page_number],
    )

    assert result.outputs == []
    assert result.register_pages == [1]
    assert result.ignored_pages == [1, 2, 3]
    assert "No invoice-like pages were detected" in result.notes[0]


def test_split_batch_file_breaks_consecutive_bills_without_register_separator(tmp_path: Path) -> None:
    source_path = tmp_path / "consecutive_bills.pdf"
    make_batch_pdf(source_path, 7)

    result = split_batch_file(
        source_path,
        register_keywords=["register"],
        ocr_text_provider=lambda _image, page_number: {
            1: "Statement Date 12/29/25 Amount Due $857.43 Eversource Account Number 74001023899 Bill To Susan White",
            2: "Page 2 of 3 Eversource Account Number 74001023899 current charges taxes total",
            3: "Page 3 of 3 Eversource Account Number 74001023899 usage continued total",
            4: "Dustbusters Invoice Number 54 Invoice Date 01/02/26 Bill To Susan White Amount Due $180.00",
            5: "Neath Landscape Construction Invoice 845 Bill To Michael White Due Date 01/30/26",
            6: "Page 2 of 4 Neath Landscape Construction Invoice 845 service period line items total",
            7: "Statement RedStick Golf Club Statement Date 01/31/26 Account Number 9988 Balance Due $500.00",
        }[page_number],
    )

    assert [output.page_numbers for output in result.outputs] == [[1, 2, 3], [4], [5, 6], [7]]
