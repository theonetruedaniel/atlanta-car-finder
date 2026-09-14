from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin

from .crawl import (
    CrawlTelemetry,
    SpiderConfig,
    classify_page,
    next_page_url,
    page_html,
    visible_text,
)
from .evidence import EvidenceContext
from .models import Observation, RunPayload, RunStatus
from .normalize import normalize_vin, parse_integer, utc_now_iso


@dataclass(frozen=True, slots=True)
class ListingSeed:
    listing_id: str
    vin: str
    year: int
    make: str
    model: str
    trim: str
    list_price: int | None
    mileage: int | None
    exterior_color: str | None
    detail_url: str


class Fetcher(Protocol):
    def fetch(self, url: str) -> object: ...


class _JsonScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[Any] = []
        self._capture = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): (value or "") for key, value in attrs}
        script_type = attributes.get("type", "").casefold()
        if tag == "script" and script_type in {
            "application/ld+json",
            "application/json",
        }:
            self._capture = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            try:
                self.documents.append(json.loads("".join(self._parts)))
            except json.JSONDecodeError:
                pass
            self._capture = False
            self._parts = []


def _json_documents(page: object) -> list[Any]:
    parser = _JsonScriptParser()
    parser.feed(page_html(page))
    return parser.documents


def _vehicles(value: Any):
    if isinstance(value, dict):
        kind = value.get("@type")
        is_embedded_listing = bool(
            (value.get("vin") or value.get("vehicleIdentificationNumber"))
            and (value.get("url") or value.get("detailUrl"))
        )
        if (
            kind == "Vehicle"
            or (isinstance(kind, list) and "Vehicle" in kind)
            or is_embedded_listing
        ):
            yield value
        for child in value.values():
            yield from _vehicles(child)
    elif isinstance(value, list):
        for child in value:
            yield from _vehicles(child)


def _name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def parse_truecar_results(page: object, url: str) -> list[ListingSeed]:
    seeds: list[ListingSeed] = []
    seen: set[str] = set()
    for document in _json_documents(page):
        for vehicle in _vehicles(document):
            try:
                vin = normalize_vin(
                    str(
                        vehicle.get("vehicleIdentificationNumber")
                        or vehicle.get("vin")
                        or ""
                    )
                )
                detail_url = urljoin(
                    url, str(vehicle.get("url") or vehicle.get("detailUrl") or "")
                )
                if vin in seen or not detail_url.startswith("https://"):
                    continue
                year = parse_integer(
                    vehicle.get("vehicleModelDate")
                    or vehicle.get("modelDate")
                    or vehicle.get("year")
                )
                if year is None:
                    continue
                offers = (
                    vehicle.get("offers")
                    if isinstance(vehicle.get("offers"), dict)
                    else {}
                )
                odometer = vehicle.get("mileageFromOdometer")
                mileage_value = (
                    odometer.get("value") if isinstance(odometer, dict) else odometer
                )
                seeds.append(
                    ListingSeed(
                        listing_id=str(
                            vehicle.get("sku")
                            or vehicle.get("productID")
                            or vehicle.get("listingId")
                            or vin
                        ),
                        vin=vin,
                        year=year,
                        make=_name(
                            vehicle.get("manufacturer")
                            or vehicle.get("brand")
                            or vehicle.get("make")
                        ),
                        model=str(vehicle.get("model") or ""),
                        trim=str(
                            vehicle.get("vehicleConfiguration")
                            or vehicle.get("trim")
                            or ""
                        ),
                        list_price=parse_integer(
                            offers.get("price")
                            if offers.get("price") is not None
                            else vehicle.get("price")
                        ),
                        mileage=parse_integer(
                            mileage_value
                            if mileage_value is not None
                            else vehicle.get("mileage")
                        ),
                        exterior_color=str(
                            vehicle.get("color") or vehicle.get("exteriorColor") or ""
                        )
                        or None,
                        detail_url=detail_url,
                    )
                )
                seen.add(vin)
            except (TypeError, ValueError):
                continue
    return seeds


def _detail_vehicle(page: object) -> dict[str, Any]:
    for document in _json_documents(page):
        for vehicle in _vehicles(document):
            return vehicle
    return {}


def _transmission(value: object) -> str:
    normalized = str(value or "").casefold()
    if "automatic" in normalized or normalized in {"a", "at", "cvt"}:
        return "automatic"
    if "manual" in normalized or normalized in {"m", "mt"}:
        return "manual"
    return "unknown"


def _dealer_kind(value: object) -> str:
    return "franchise" if str(value or "").casefold() == "franchise" else "independent"


def parse_truecar_detail(page: object, seed: ListingSeed) -> Observation:
    vehicle = _detail_vehicle(page)
    evidence = EvidenceContext.from_html(page)
    title = evidence.claim(
        "Clean title", positive_patterns=(r"Clean title reported",)
    )
    accident = evidence.claim(
        "No accidents reported",
        positive_patterns=(r"No accidents? reported",),
    )
    accident_negative = evidence.first(
        (
            r"(?:an?\s+)?accidents?\s+reported",
            r"(?:\d+|one|multiple)\s+accidents?\s+reported",
            r"Accident status\s*[:=-]\s*reported",
        ),
    )
    carplay = evidence.claim("Apple CarPlay")
    backup = evidence.claim("Backup Camera")
    assist_pairs = (
        ("Adaptive Cruise Control", "adaptive-cruise"),
        ("Lane Departure Warning", "lane-departure"),
        ("Lane Keep Assist", "lane-keep"),
        ("Active Lane Assist", "active-lane"),
        ("Lane Centering", "lane-centering"),
    )
    assist_claims = [
        (feature, evidence.claim(phrase))
        for phrase, feature in assist_pairs
    ]
    assist_evidence = [
        claim.evidence
        for _, claim in assist_claims
        if claim.value is not None and claim.evidence is not None
    ]
    assist = tuple(
        feature for feature, claim in assist_claims if claim.value is True
    )
    offers = vehicle.get("offers") if isinstance(vehicle.get("offers"), dict) else {}
    seller = offers.get("seller") if isinstance(offers.get("seller"), dict) else {}
    images = vehicle.get("image") or ()
    if isinstance(images, str):
        images = (images,)
    if not isinstance(images, (list, tuple)):
        images = ()
    detail_vin = vehicle.get("vehicleIdentificationNumber") or seed.vin
    year = parse_integer(vehicle.get("vehicleModelDate")) or seed.year
    odometer = vehicle.get("mileageFromOdometer")
    mileage_value = odometer.get("value") if isinstance(odometer, dict) else odometer
    canonical_url = urljoin(seed.detail_url, str(vehicle.get("url") or seed.detail_url))
    negative_title = evidence.first(
        (
            r"(?:salvage|rebuilt|branded|flood)\s+title(?:\s+reported)?",
            r"Title status\s*[:=-]\s*(?:salvage|rebuilt|branded|flood)",
        ),
    )
    lemon_negative = evidence.first(
        (
            r"(?:lemon(?:\s+law)?|manufacturer)\s+buyback(?:\s+reported)?",
            r"lemon\s+(?:history|title|status)(?:\s+was)?\s+reported",
        ),
    )
    price = (
        parse_integer(offers.get("price"))
        if offers.get("price") is not None
        else seed.list_price
    )
    mileage = (
        parse_integer(mileage_value) if mileage_value is not None else seed.mileage
    )
    if price is None or mileage is None:
        raise ValueError("listing requires explicit price and mileage")

    return Observation(
        source="TrueCar",
        listing_id=str(vehicle.get("sku") or seed.listing_id),
        vin=str(detail_vin),
        year=year,
        make=_name(vehicle.get("manufacturer") or vehicle.get("brand")) or seed.make,
        model=str(vehicle.get("model") or seed.model),
        trim=str(
            vehicle.get("vehicleConfiguration") or vehicle.get("trim") or seed.trim
        ),
        body_style=str(vehicle.get("bodyType") or ""),
        transmission=_transmission(vehicle.get("vehicleTransmission")),
        list_price=price,
        mileage=mileage,
        distance_miles=None,
        local_delivery=False,
        exterior_color=str(vehicle.get("color") or seed.exterior_color or ""),
        dealer_name=str(seller.get("name") or ""),
        dealer_kind=_dealer_kind(seller.get("dealerKind")),
        dealer_accepted=None,
        dealer_rating=None,
        dealer_review_count=None,
        image_urls=tuple(
            str(image) for image in images if str(image).startswith("https://")
        ),
        canonical_url=canonical_url,
        clean_title=False if negative_title else title.value,
        title_evidence=negative_title or title.evidence,
        lemon_free=False if lemon_negative else None,
        lemon_evidence=lemon_negative,
        major_accident_free=(
            False if accident_negative else accident.value
        ),
        accident_evidence=accident_negative or accident.evidence,
        carplay=carplay.value,
        carplay_evidence=carplay.evidence,
        backup_camera=backup.value,
        backup_camera_evidence=backup.evidence,
        driver_assist=assist,
        driver_assist_evidence="; ".join(assist_evidence) or None,
        required_evidence_conflict=bool(
            title.conflict
            or (title.value is True and negative_title)
            or accident.conflict
            or (accident.value is True and accident_negative)
            or carplay.conflict
            or backup.conflict
            or any(claim.conflict for _, claim in assist_claims)
        ),
        fee_evidence=None,
    )


def _reported_total_pages(page: object) -> int | None:
    match = re.search(
        r"\bPage\s+\d+\s+of\s+(\d+)\b", visible_text(page), flags=re.IGNORECASE
    )
    return int(match.group(1)) if match else None


def _run(
    config: SpiderConfig,
    fetcher: Fetcher,
    monotonic: Callable[[], float],
) -> RunPayload:
    started_at = utc_now_iso()
    telemetry = CrawlTelemetry(max_pages=config.max_pages)
    observations: list[Observation] = []
    quarantined = 0
    current_url: str | None = config.start_url
    status = RunStatus.COMPLETE
    error_summary: str | None = None

    while current_url:
        if (
            config.deadline_monotonic is not None
            and monotonic() >= config.deadline_monotonic
        ):
            status = (
                RunStatus.FAILED
                if telemetry.pages_completed == 0
                else RunStatus.PARTIAL
            )
            error_summary = "internal crawl deadline reached"
            break
        if not telemetry.begin_page(current_url):
            status = RunStatus.PARTIAL
            error_summary = (
                "pagination repeated a URL or reached the configured safety limit"
            )
            break
        try:
            page = fetcher.fetch(current_url)
        except Exception as exc:  # noqa: BLE001 - every fetcher failure must become telemetry
            status = (
                RunStatus.FAILED
                if telemetry.pages_completed == 0
                else RunStatus.PARTIAL
            )
            error_summary = f"fetch failed: {type(exc).__name__}"
            break
        classification = classify_page(page)
        if classification == "blocked":
            status = (
                RunStatus.BLOCKED
                if telemetry.pages_completed == 0
                else RunStatus.PARTIAL
            )
            error_summary = "marketplace blocked a results page"
            break
        telemetry.complete_page()
        page_total = _reported_total_pages(page)
        if page_total is not None:
            if telemetry.reported_total_pages is None:
                telemetry.reported_total_pages = page_total
            elif page_total != telemetry.reported_total_pages:
                previous_total = telemetry.reported_total_pages
                telemetry.reported_total_pages = max(previous_total, page_total)
                status = RunStatus.PARTIAL
                error_summary = (
                    "marketplace reported inconsistent result page totals "
                    f"({previous_total} then {page_total})"
                )
        if classification == "empty":
            break

        if (
            config.deadline_monotonic is not None
            and monotonic() >= config.deadline_monotonic
        ):
            status = RunStatus.PARTIAL
            error_summary = "internal crawl deadline reached"
            break

        seeds = parse_truecar_results(page, current_url)
        if not seeds:
            status = RunStatus.PARTIAL
            error_summary = "non-empty results page contained no listing seeds"
            break
        for seed in seeds:
            if (
                config.deadline_monotonic is not None
                and monotonic() >= config.deadline_monotonic
            ):
                status = RunStatus.PARTIAL
                error_summary = "internal crawl deadline reached"
                break
            if config.delay_seconds:
                time.sleep(config.delay_seconds)
            try:
                detail = fetcher.fetch(seed.detail_url)
            except Exception as exc:  # noqa: BLE001 - browser transports use varied exception types
                status = RunStatus.PARTIAL
                error_summary = f"listing detail fetch failed: {type(exc).__name__}"
                break
            if classify_page(detail) == "blocked":
                status = RunStatus.PARTIAL
                error_summary = "marketplace blocked a listing detail page"
                break
            try:
                observations.append(parse_truecar_detail(detail, seed))
            except (TypeError, ValueError):
                quarantined += 1
            if (
                config.deadline_monotonic is not None
                and monotonic() >= config.deadline_monotonic
            ):
                status = RunStatus.PARTIAL
                error_summary = "internal crawl deadline reached"
                break
        if status != RunStatus.COMPLETE:
            break
        following = next_page_url(page, current_url)
        if following is None:
            if (
                telemetry.reported_total_pages is not None
                and telemetry.pages_completed < telemetry.reported_total_pages
            ):
                status = RunStatus.PARTIAL
                error_summary = (
                    "reported result pages remain but no next page link was found"
                )
            break
        current_url = following

    if (
        status == RunStatus.COMPLETE
        and telemetry.reported_total_pages is not None
        and telemetry.pages_completed != telemetry.reported_total_pages
    ):
        status = RunStatus.PARTIAL
        error_summary = (
            f"completed {telemetry.pages_completed} result pages but marketplace "
            f"reported {telemetry.reported_total_pages}"
        )

    if quarantined:
        quarantine_summary = (
            f"{quarantined} malformed listing"
            f"{' was' if quarantined == 1 else 's were'} quarantined"
        )
        if status == RunStatus.COMPLETE:
            status = RunStatus.PARTIAL
        error_summary = ". ".join(filter(None, (error_summary, quarantine_summary)))

    finished_at = utc_now_iso()
    run_id = f"truecar-{started_at.replace('-', '').replace(':', '')}"
    return RunPayload(
        source="TrueCar",
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        pages_attempted=telemetry.pages_attempted,
        pages_completed=telemetry.pages_completed,
        reported_total_pages=telemetry.reported_total_pages,
        status=status,
        error_summary=error_summary,
        observations=tuple(observations),
        quarantined_count=quarantined,
    )


def run_truecar(
    config: SpiderConfig,
    *,
    fetcher: Fetcher | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> RunPayload:
    if fetcher is not None:
        return _run(config, fetcher, monotonic)
    from scrapling.fetchers import StealthySession

    with StealthySession(headless=True, max_pages=1) as session:
        return _run(config, session, monotonic)

