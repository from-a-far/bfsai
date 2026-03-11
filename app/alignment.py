from __future__ import annotations

from typing import Any

from .fields import TRACKED_FIELDS
from .schemas import OcrResult
from .utils import normalize_text


class AlignmentService:
    def align_extraction(self, extraction: dict[str, Any], ocr_result: OcrResult) -> dict[str, Any]:
        aligned = {
            field_name: self._align_value(extraction.get(field_name), ocr_result)
            for field_name in TRACKED_FIELDS
        }
        aligned["line_items"] = [
            {
                "description": self._align_value(item.get("description"), ocr_result),
                "amount": self._align_value(item.get("amount"), ocr_result),
            }
            for item in extraction.get("line_items", [])
        ]
        return aligned

    def _align_value(self, value: Any, ocr_result: OcrResult) -> dict[str, Any] | None:
        if value in (None, "", []):
            return None
        candidates = self._candidate_tokens(value)
        best_match: dict[str, Any] | None = None
        for page in ocr_result.pages:
            words = page.words
            for candidate in candidates:
                candidate_tokens = [token for token in candidate.split() if token]
                if not candidate_tokens:
                    continue
                for start in range(len(words)):
                    match_words = self._match_sequence(words, start, candidate_tokens)
                    if not match_words:
                        continue
                    left = min(word.left for word in match_words)
                    top = min(word.top for word in match_words)
                    right = max(word.left + word.width for word in match_words)
                    bottom = max(word.top + word.height for word in match_words)
                    score = round(len(match_words) / len(candidate_tokens), 2)
                    current = {
                        "value": value,
                        "page_number": page.page_number,
                        "match_text": " ".join(word.text for word in match_words),
                        "bbox": {
                            "left": left,
                            "top": top,
                            "width": right - left,
                            "height": bottom - top,
                        },
                        "normalized_bbox": {
                            "left": round(left / max(page.width, 1), 4),
                            "top": round(top / max(page.height, 1), 4),
                            "width": round((right - left) / max(page.width, 1), 4),
                            "height": round((bottom - top) / max(page.height, 1), 4),
                        },
                        "confidence": score,
                    }
                    if not best_match or current["confidence"] > best_match["confidence"]:
                        best_match = current
        return best_match

    def _candidate_tokens(self, value: Any) -> list[str]:
        if isinstance(value, float):
            amount = f"{value:.2f}"
            return [
                normalize_text(amount),
                normalize_text(f"${amount}"),
                normalize_text(f"{value:,.2f}"),
                normalize_text(f"${value:,.2f}"),
            ]
        normalized = normalize_text(str(value))
        return [normalized]

    def _match_sequence(self, words: list[Any], start: int, candidate_tokens: list[str]) -> list[Any]:
        matched: list[Any] = []
        cursor = start
        for token in candidate_tokens:
            if cursor >= len(words):
                return []
            normalized_word = words[cursor].normalized_text
            if token == normalized_word or token in normalized_word or normalized_word in token:
                matched.append(words[cursor])
                cursor += 1
                continue
            return []
        return matched
