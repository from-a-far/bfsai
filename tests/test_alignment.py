from app.alignment import AlignmentService
from app.schemas import OcrPage, OcrResult, OcrWord


def test_alignment_finds_vendor_and_total() -> None:
    result = OcrResult(
        text="Acme Inc Total 123.45",
        page_count=1,
        pages=[
            OcrPage(
                page_number=1,
                width=1000,
                height=1000,
                text="Acme Inc Total 123.45",
                words=[
                    OcrWord(text="Acme", normalized_text="acme", page_number=1, left=10, top=10, width=60, height=20, confidence=95),
                    OcrWord(text="Inc", normalized_text="inc", page_number=1, left=75, top=10, width=40, height=20, confidence=95),
                    OcrWord(text="Total", normalized_text="total", page_number=1, left=10, top=50, width=55, height=20, confidence=95),
                    OcrWord(text="123.45", normalized_text="123.45", page_number=1, left=70, top=50, width=70, height=20, confidence=95),
                ],
            )
        ],
    )
    aligned = AlignmentService().align_extraction({"vendor": "Acme Inc", "total": 123.45, "line_items": []}, result)
    assert aligned["vendor"]["page_number"] == 1
    assert aligned["total"]["match_text"] == "123.45"


def test_alignment_extracts_values_from_learned_profiles() -> None:
    result = OcrResult(
        text="Acme Incorporated 123 Main St Suite 200",
        page_count=1,
        pages=[
            OcrPage(
                page_number=1,
                width=1000,
                height=1000,
                text="Acme Incorporated\n123 Main St\nSuite 200",
                words=[
                    OcrWord(text="Acme", normalized_text="acme", page_number=1, left=100, top=100, width=70, height=24, confidence=95),
                    OcrWord(text="Incorporated", normalized_text="incorporated", page_number=1, left=180, top=100, width=150, height=24, confidence=95),
                    OcrWord(text="123", normalized_text="123", page_number=1, left=100, top=220, width=35, height=24, confidence=95),
                    OcrWord(text="Main", normalized_text="main", page_number=1, left=145, top=220, width=55, height=24, confidence=95),
                    OcrWord(text="St", normalized_text="st", page_number=1, left=210, top=220, width=25, height=24, confidence=95),
                    OcrWord(text="Suite", normalized_text="suite", page_number=1, left=100, top=255, width=55, height=24, confidence=95),
                    OcrWord(text="200", normalized_text="200", page_number=1, left=165, top=255, width=40, height=24, confidence=95),
                ],
            )
        ],
    )
    profiles = [
        {
            "field_name": "vendor",
            "page_number": 1,
            "page_count": 1,
            "normalized_bbox": {"left": 0.09, "top": 0.08, "width": 0.28, "height": 0.06},
            "sample_count": 3,
        },
        {
            "field_name": "billing_address",
            "page_number": 1,
            "page_count": 1,
            "normalized_bbox": {"left": 0.09, "top": 0.2, "width": 0.18, "height": 0.1},
            "sample_count": 2,
        },
    ]

    extracted = AlignmentService().extract_from_profiles(profiles, result)

    assert extracted["vendor"]["value"] == "Acme Incorporated"
    assert extracted["billing_address"]["value"] == "123 Main St\nSuite 200"
