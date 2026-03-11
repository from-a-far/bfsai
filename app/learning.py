from __future__ import annotations

from typing import Any

from .fields import TRACKED_FIELDS
from .repository import Repository
from .utils import normalize_vendor


class LearningService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def build_hints(self, po_box: str, text: str) -> dict[str, Any]:
        profiles = self.repository.get_vendor_profiles(po_box)
        text_norm = normalize_vendor(text[:4000])
        best_match: dict[str, Any] | None = None
        best_score = 0
        for profile in profiles:
            vendor_norm = profile["normalized_vendor"]
            if not vendor_norm:
                continue
            overlap = sum(1 for token in vendor_norm.split() if token in text_norm)
            if overlap > best_score:
                best_score = overlap
                best_match = profile
        if not best_match:
            return {}
        return {
            "matched_vendor": best_match["display_vendor"],
            "confirmed_fields": best_match["confirmed_fields"],
            "approved_count": best_match["approved_count"],
            "correction_count": best_match["correction_count"],
        }

    def record_confirmation(
        self,
        document: dict[str, Any],
        corrected_extraction: dict[str, Any],
    ) -> int:
        previous = document["extraction"]
        po_box = document["po_box"]
        correction_count = 0
        for field in TRACKED_FIELDS:
            old_value = previous.get(field)
            new_value = corrected_extraction.get(field)
            if old_value != new_value:
                correction_count += 1
                self.repository.record_correction(
                    document_id=document["id"],
                    po_box=po_box,
                    field_name=field,
                    old_value=old_value,
                    new_value=new_value,
                )

        vendor_name = corrected_extraction.get("vendor") or previous.get("vendor")
        vendor_norm = normalize_vendor(vendor_name)
        if vendor_norm:
            profiles = {
                profile["normalized_vendor"]: profile
                for profile in self.repository.get_vendor_profiles(po_box)
            }
            existing = profiles.get(vendor_norm)
            confirmed_fields = dict(existing["confirmed_fields"]) if existing else {}
            for field in TRACKED_FIELDS:
                value = corrected_extraction.get(field)
                if value not in (None, "", []):
                    confirmed_fields[field] = value
            approved_count = (existing["approved_count"] if existing else 0) + 1
            total_corrections = (existing["correction_count"] if existing else 0) + correction_count
            self.repository.upsert_vendor_profile(
                po_box=po_box,
                normalized_vendor=vendor_norm,
                display_vendor=vendor_name,
                approved_count=approved_count,
                correction_count=total_corrections,
                confirmed_fields=confirmed_fields,
            )
        return correction_count
