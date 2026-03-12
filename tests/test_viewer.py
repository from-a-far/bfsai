from app.viewer import normalize_extracted_value


def test_normalize_extracted_value_preserves_newlines_for_multiline_fields() -> None:
    value = normalize_extracted_value("billing_address", "Line 1\nLine 2\n\nLine 3")
    assert value == "Line 1\nLine 2\nLine 3"


def test_normalize_extracted_value_still_compacts_single_line_fields() -> None:
    value = normalize_extracted_value("vendor", "Acme   Incorporated\nLLC")
    assert value == "Acme Incorporated LLC"
