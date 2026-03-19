from io import BytesIO

from PIL import Image

from app.viewer import normalize_extracted_value, render_page_png


def test_normalize_extracted_value_preserves_newlines_for_multiline_fields() -> None:
    value = normalize_extracted_value("billing_address", "Line 1\nLine 2\n\nLine 3")
    assert value == "Line 1\nLine 2\nLine 3"


def test_normalize_extracted_value_still_compacts_single_line_fields() -> None:
    value = normalize_extracted_value("vendor", "Acme   Incorporated\nLLC")
    assert value == "Acme Incorporated LLC"


def test_render_page_png_respects_max_width(tmp_path) -> None:
    source_path = tmp_path / "page.png"
    Image.new("RGB", (1800, 900), color=(20, 40, 60)).save(source_path, format="PNG")

    png = render_page_png(source_path, 1, max_width=600)

    with Image.open(BytesIO(png)) as rendered:
        assert rendered.width == 600
        assert rendered.height == 300
