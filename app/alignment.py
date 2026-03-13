from __future__ import annotations

from typing import Any

from .fields import TRACKED_FIELDS
from .schemas import OcrResult
from .utils import normalize_text
from .viewer import normalize_extracted_value


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

    def extract_from_profiles(
        self,
        field_profiles: list[dict[str, Any]],
        ocr_result: OcrResult,
    ) -> dict[str, dict[str, Any]]:
        if not field_profiles:
            return {}
        page_count = ocr_result.page_count
        matched_profiles = [
            profile
            for profile in field_profiles
            if int(profile.get("page_count") or 1) == page_count
        ]
        if not matched_profiles:
            matched_profiles = list(field_profiles)
        extracted: dict[str, dict[str, Any]] = {}
        for profile in matched_profiles:
            field_name = str(profile.get("field_name") or "")
            if field_name not in TRACKED_FIELDS:
                continue
            current = extracted.get(field_name)
            if current and int(current.get("sample_count") or 0) >= int(profile.get("sample_count") or 0):
                continue
            candidate = self._extract_profile_value(field_name, profile, ocr_result)
            if candidate:
                extracted[field_name] = candidate
        return extracted

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

    def _extract_profile_value(
        self,
        field_name: str,
        profile: dict[str, Any],
        ocr_result: OcrResult,
    ) -> dict[str, Any] | None:
        page_number = int(profile.get("page_number") or 1)
        page = next((page for page in ocr_result.pages if page.page_number == page_number), None)
        normalized_bbox = profile.get("normalized_bbox") or {}
        if not page or not isinstance(normalized_bbox, dict):
            return None
        left = float(normalized_bbox.get("left") or 0) * max(page.width, 1)
        top = float(normalized_bbox.get("top") or 0) * max(page.height, 1)
        width = float(normalized_bbox.get("width") or 0) * max(page.width, 1)
        height = float(normalized_bbox.get("height") or 0) * max(page.height, 1)
        if width <= 0 or height <= 0:
            return None
        right = left + width
        bottom = top + height
        matched_words = [
            word
            for word in page.words
            if self._word_center_in_box(word, left, top, right, bottom)
        ]
        if not matched_words:
            return None
        raw_text = self._words_to_text(matched_words)
        value = normalize_extracted_value(field_name, raw_text)
        if value in (None, "", []):
            return None
        return {
            "value": value,
            "page_number": page_number,
            "match_text": raw_text,
            "normalized_bbox": normalized_bbox,
            "confidence": min(0.99, 0.65 + min(int(profile.get("sample_count") or 1), 5) * 0.05),
            "sample_count": int(profile.get("sample_count") or 1),
            "source": "vendor_field_profile",
        }

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

    def _word_center_in_box(
        self,
        word: Any,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> bool:
        center_x = word.left + (word.width / 2)
        center_y = word.top + (word.height / 2)
        return left <= center_x <= right and top <= center_y <= bottom

    def _words_to_text(self, words: list[Any]) -> str:
        ordered = sorted(words, key=lambda word: (word.top, word.left))
        lines: list[list[str]] = []
        current_line: list[str] = []
        current_top: float | None = None
        tolerance = max(min((sum(word.height for word in ordered) / max(len(ordered), 1)) * 0.6, 24), 8)
        for word in ordered:
            if current_top is None or abs(word.top - current_top) <= tolerance:
                current_line.append(word.text)
                current_top = word.top if current_top is None else min(current_top, word.top)
                continue
            lines.append(current_line)
            current_line = [word.text]
            current_top = word.top
        if current_line:
            lines.append(current_line)
        return "\n".join(" ".join(line).strip() for line in lines if line).strip()

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
