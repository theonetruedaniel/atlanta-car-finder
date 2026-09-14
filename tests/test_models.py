import json
from dataclasses import asdict

import pytest

from car_finder_scrapers.models import Observation, RunPayload, RunStatus
from car_finder_scrapers.normalize import normalize_vin, parse_integer, utc_now_iso


def observation(**overrides):
    values = {
        "source": "TrueCar",
        "listing_id": "tc-42",
        "vin": "1hgcm82633a004352",
        "year": 2021,
        "make": "Honda",
        "model": "Accord",
        "trim": "EX-L",
        "body_style": "Sedan",
        "transmission": "automatic",
        "list_price": 24_500,
        "mileage": 31_200,
        "distance_miles": None,
        "local_delivery": False,
        "exterior_color": "Crystal Black Pearl",
        "dealer_name": "Example Honda",
        "dealer_kind": "franchise",
        "dealer_accepted": None,
        "dealer_rating": None,
        "dealer_review_count": None,
        "image_urls": ("https://www.truecar.com/image/42.jpg",),
        "canonical_url": "https://www.truecar.com/used-cars-for-sale/listing/1HGCM82633A004352/",
        "clean_title": None,
        "title_evidence": None,
        "lemon_free": None,
        "lemon_evidence": None,
        "major_accident_free": None,
        "accident_evidence": None,
        "carplay": None,
        "carplay_evidence": None,
        "backup_camera": None,
        "backup_camera_evidence": None,
        "driver_assist": (),
        "driver_assist_evidence": None,
        "required_evidence_conflict": False,
        "fee_evidence": None,
    }
    values.update(overrides)
    return Observation(**values)


def test_normalize_vin_trims_and_uppercases():
    assert normalize_vin(" 1hgcm82633a004352 ") == "1HGCM82633A004352"


def test_normalize_vin_rejects_i_o_q():
    with pytest.raises(ValueError, match="VIN"):
        normalize_vin("1HGCM82633A00I352")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("$12,345", 12_345), ("87,001 miles", 87_001), (1200, 1200), (None, None)],
)
def test_parse_integer_handles_explicit_numeric_values(raw, expected):
    assert parse_integer(raw) == expected


def test_parse_integer_rejects_values_without_digits():
    with pytest.raises(ValueError, match="integer"):
        parse_integer("Call for price")


def test_observation_preserves_unknown_evidence_conservatively():
    item = observation()

    assert item.clean_title is None
    assert item.lemon_free is None
    assert item.major_accident_free is None
    assert item.carplay is None
    assert item.title_evidence is None
    assert item.driver_assist == ()


def test_observation_requires_evidence_fields_explicitly():
    values = asdict(observation())
    values.pop("title_evidence")

    with pytest.raises(TypeError):
        Observation(**values)


@pytest.mark.parametrize(
    ("source", "url"),
    [
        ("TrueCar", "http://www.truecar.com/listing/42"),
        ("TrueCar", "https://truecar.example/listing/42"),
        ("CarGurus", "https://www.truecar.com/listing/42"),
    ],
)
def test_observation_rejects_unsafe_or_mismatched_source_urls(source, url):
    with pytest.raises(ValueError, match="URL"):
        observation(source=source, canonical_url=url)


def test_run_payload_json_is_deterministic_and_uses_contract_names():
    payload = RunPayload(
        source="TrueCar",
        run_id="truecar-20260820T100000Z",
        started_at="2026-08-20T10:00:00Z",
        finished_at="2026-08-20T10:02:00Z",
        pages_attempted=2,
        pages_completed=2,
        reported_total_pages=2,
        status=RunStatus.COMPLETE,
        error_summary=None,
        observations=(observation(),),
        quarantined_count=0,
    )

    encoded = payload.to_json()

    assert encoded == json.dumps(
        payload.to_dict(), sort_keys=True, separators=(",", ":")
    )
    assert json.loads(encoded)["pagesAttempted"] == 2
    assert json.loads(encoded)["totalPages"] == 2
    assert json.loads(encoded)["observations"][0]["vin"] == "1HGCM82633A004352"
    assert json.loads(encoded)["observations"][0]["evidence"]["cleanTitle"] is None


def test_utc_now_iso_is_timezone_qualified():
    stamp = utc_now_iso()
    assert stamp.endswith("Z")
    assert "+" not in stamp

