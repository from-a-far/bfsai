from app.classifier import classify_document


def test_classify_bill_when_amount_due_present() -> None:
    routing = classify_document("Invoice summary Amount Due $123.45 Payment due immediately")
    assert routing.dtype == "b"
    assert routing.should_extract is True
    assert routing.duplicate_to_type_folder is False


def test_classify_statement_without_amount_due_as_bypass() -> None:
    routing = classify_document("Statement period 01/01/2026 to 01/31/2026 Beginning balance Ending balance")
    assert routing.dtype == "s"
    assert routing.should_extract is False


def test_classify_tax_scan_as_process_and_duplicate() -> None:
    routing = classify_document("IRS Form 1099-INT Tax Year 2025")
    assert routing.dtype == "t"
    assert routing.should_extract is False
    assert routing.duplicate_to_type_folder is False


def test_classify_credit_card_statement_for_new_and_copy() -> None:
    routing = classify_document("Cardmember statement minimum payment due 02/01/2026 available credit")
    assert routing.dtype == "cc"
    assert routing.should_extract is True
    assert routing.duplicate_to_type_folder is True
