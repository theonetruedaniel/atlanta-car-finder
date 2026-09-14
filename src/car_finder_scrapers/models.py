from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from urllib.parse import urlparse

from .normalize import normalize_vin

SOURCE_DOMAINS = {
    "CarGurus": "cargurus.com",
    "TrueCar": "truecar.com",
}


class RunStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


def _validate_url(value: str, *, source: str | None = None) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTPS URL")
    if source is not None:
        expected = SOURCE_DOMAINS.get(source)
        if expected is None:
            raise ValueError(f"unsupported source: {source}")
        hostname = parsed.hostname.lower()
        if hostname != expected and not hostname.endswith(f".{expected}"):
            raise ValueError(f"URL does not match the {source} domain")


@dataclass(frozen=True, slots=True)
class Observation:
    source: str
    listing_id: str
    vin: str
    year: int
    make: str
    model: str
    trim: str
    body_style: str
    transmission: Literal["automatic", "manual", "unknown"]
    list_price: int
    mileage: int
    distance_miles: float | None
    local_delivery: bool
    exterior_color: str | None
    dealer_name: str | None
    dealer_kind: Literal["franchise", "independent"]
    dealer_accepted: bool | None
    dealer_rating: float | None
    dealer_review_count: int | None
    image_urls: tuple[str, ...]
    canonical_url: str
    clean_title: bool | None
    title_evidence: str | None
    lemon_free: bool | None
    lemon_evidence: str | None
    major_accident_free: bool | None
    accident_evidence: str | None
    carplay: bool | None
    carplay_evidence: str | None
    backup_camera: bool | None
    backup_camera_evidence: str | None
    driver_assist: tuple[str, ...]
    driver_assist_evidence: str | None
    required_evidence_conflict: bool
    fee_evidence: str | None

    def __post_init__(self) -> None:
        if self.source not in SOURCE_DOMAINS:
            raise ValueError(f"unsupported source: {self.source}")
        object.__setattr__(self, "vin", normalize_vin(self.vin))
        _validate_url(self.canonical_url, source=self.source)
        for url in self.image_urls:
            _validate_url(url, source=self.source)
        if self.year < 1886:
            raise ValueError("year is outside the valid vehicle range")
        if not self.listing_id.strip():
            raise ValueError("listing ID is required")
        if self.transmission not in {"automatic", "manual", "unknown"}:
            raise ValueError("transmission is unsupported")
        if self.dealer_kind not in {"franchise", "independent"}:
            raise ValueError("dealer kind is unsupported")
        if self.list_price < 0 or self.mileage < 0:
            raise ValueError("price and mileage cannot be negative")

    def to_dict(self) -> dict[str, object]:
        history = "; ".join(
            evidence
            for evidence in (
                self.title_evidence,
                self.lemon_evidence,
                self.accident_evidence,
                self.fee_evidence,
            )
            if evidence
        )
        return {
            "vin": self.vin,
            "listingUrl": self.canonical_url,
            "historyUrl": self.canonical_url,
            "imageUrls": list(self.image_urls),
            "year": self.year,
            "make": self.make,
            "model": self.model,
            "trim": self.trim,
            "bodyStyle": self.body_style,
            "transmission": self.transmission,
            "listPrice": self.list_price,
            "mileage": self.mileage,
            "distanceMiles": self.distance_miles,
            "localDelivery": self.local_delivery,
            "exteriorColor": self.exterior_color,
            "dealerName": self.dealer_name,
            "dealerKind": self.dealer_kind,
            "dealerAccepted": self.dealer_accepted,
            "dealerRating": self.dealer_rating,
            "dealerReviewCount": self.dealer_review_count,
            "evidence": {
                "cleanTitle": self.clean_title,
                "lemonFree": self.lemon_free,
                "majorAccidentFree": self.major_accident_free,
                "carPlay": self.carplay,
                "backupCamera": self.backup_camera,
                "driverAssist": list(self.driver_assist),
                "history": history,
                "carPlayDetail": self.carplay_evidence or "",
                "backupCameraDetail": self.backup_camera_evidence or "",
                "driverAssistDetail": self.driver_assist_evidence or "",
            },
        }


@dataclass(frozen=True, slots=True)
class RunPayload:
    source: str
    run_id: str
    started_at: str
    finished_at: str
    pages_attempted: int
    pages_completed: int
    reported_total_pages: int | None
    status: RunStatus
    error_summary: str | None
    observations: tuple[Observation, ...]
    quarantined_count: int

    def __post_init__(self) -> None:
        if self.source not in SOURCE_DOMAINS:
            raise ValueError(f"unsupported source: {self.source}")
        if any(item.source != self.source for item in self.observations):
            raise ValueError("observation source does not match run source")
        if self.pages_attempted < 0 or self.pages_completed < 0:
            raise ValueError("page counts cannot be negative")
        if self.pages_completed > self.pages_attempted:
            raise ValueError("pages completed cannot exceed pages attempted")
        if self.quarantined_count < 0:
            raise ValueError("quarantined count cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "runId": self.run_id,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "pagesAttempted": self.pages_attempted,
            "pagesCompleted": self.pages_completed,
            "totalPages": self.reported_total_pages,
            "status": self.status.value,
            "errorSummary": self.error_summary,
            "observations": [item.to_dict() for item in self.observations],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

