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
