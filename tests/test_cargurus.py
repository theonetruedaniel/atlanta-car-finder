from pathlib import Path

import pytest

from car_finder_scrapers.cargurus import (
    parse_cargurus_detail,
    parse_cargurus_results,
    run_cargurus,
)
from car_finder_scrapers.crawl import SpiderConfig
from car_finder_scrapers.models import RunStatus

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS_URL = "https://www.cargurus.com/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action?zip=30303"


def fixture(name):
    return (FIXTURES / name).read_text()


def test_parse_cargurus_results_extracts_structured_listing_seed():
    seeds = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)

    assert len(seeds) == 1
    assert seeds[0].vin == "2HKRW2H89MH600001"
    assert seeds[0].listing_id == "CG-200"
    assert seeds[0].year == 2021
    assert (seeds[0].make, seeds[0].model, seeds[0].trim) == (
        "Honda",
        "CR-V",
        "EX-L AWD",
    )
    assert seeds[0].list_price == 23_750
    assert seeds[0].mileage == 42_105
    assert seeds[0].exterior_color == "Modern Steel Metallic"
    assert seeds[0].detail_url == "https://www.cargurus.com/Cars/link/CG-200"


def test_parse_cargurus_results_supports_embedded_listing_state():
    page = """
      <script type="application/json">
        {"results":[{"vin":"1FMCU0GDXJUD33886","listingId":"CG-STATE",
        "url":"/Cars/link/CG-STATE","year":2018,"make":"Ford","model":"Escape",
        "trim":"SE","price":9995,"mileage":94200,"exteriorColor":"Blue"}]}
      </script>
    """

    seeds = parse_cargurus_results(page, RESULTS_URL)

    assert len(seeds) == 1
    assert seeds[0].listing_id == "CG-STATE"
    assert seeds[0].list_price == 9_995
    assert seeds[0].mileage == 94_200


def test_parse_cargurus_detail_extracts_autocheck_options_photos_and_fees():
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    item = parse_cargurus_detail(fixture("cargurus-detail.html"), seed)

    assert item.listing_id == "CG-200"
    assert item.vin == "2HKRW2H89MH600001"
    assert item.list_price == 23_750
    assert item.mileage == 42_105
    assert item.exterior_color == "Modern Steel Metallic"
    assert item.dealer_name == "Metro Honda"
    assert item.dealer_kind == "franchise"
    assert item.body_style == "SUV"
    assert item.transmission == "automatic"
    assert item.image_urls == ("https://www.cargurus.com/images/CG-200-front.jpg",)
    assert item.clean_title is True
    assert item.title_evidence == "Clean title reported"
    assert item.lemon_free is True
    assert item.lemon_evidence == "No lemon history reported"
    assert item.major_accident_free is True
    assert item.accident_evidence == "No accidents reported"
    assert item.carplay is True
    assert item.backup_camera is True
    assert item.driver_assist == ("lane-departure",)
    assert item.fee_evidence == "Dealer documentation fee: $899"


def test_parse_cargurus_detail_preserves_negative_conflict_evidence():
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    page = """
      <script type="application/json">
        {"listing":{"autoCheck":{"title":"Clean title reported"},"titleBrand":"Salvage title reported"}}
      </script>
    """

    item = parse_cargurus_detail(page, seed)

    assert item.clean_title is False
    assert item.title_evidence == "Salvage title reported"
    assert item.required_evidence_conflict is True


def test_parse_cargurus_detail_maps_explicit_negative_history_to_false():
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    page = """
      <script type="application/json">
        {"listing":{"autoCheck":{
          "title":"Salvage title reported",
          "lemon":"Lemon history reported",
          "accidents":"1 accident reported"
        }}}
      </script>
    """

    item = parse_cargurus_detail(page, seed)

    assert item.clean_title is False
    assert item.title_evidence == "Salvage title reported"
    assert item.lemon_free is False
    assert item.lemon_evidence == "Lemon history reported"
    assert item.major_accident_free is False
    assert item.accident_evidence == "1 accident reported"


@pytest.mark.parametrize(
    ("key", "value", "field", "expected"),
    [
        ("title", "Clean title status: no", "clean_title", False),
        ("title", "Clean title unavailable", "clean_title", False),
        ("accidents", "No accidents reported: false", "major_accident_free", False),
        ("lemon", "No lemon status: false", "lemon_free", False),
        ("title", "Clean title reported", "clean_title", True),
        ("accidents", "No accidents reported", "major_accident_free", True),
        ("lemon", "No lemon history reported", "lemon_free", True),
    ],
)
def test_parse_cargurus_detail_uses_bounded_structured_history_values(
    key, value, field, expected
):
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    page = f"""
      <script type="application/json">
        {{"listing":{{"autoCheck":{{"{key}":"{value}"}}}}}}
      </script>
    """

    item = parse_cargurus_detail(page, seed)

    assert getattr(item, field) is expected
    assert value in item.to_dict()["evidence"]["history"]


@pytest.mark.parametrize(
    ("key", "value", "field"),
    [
        ("title", "Clean title information pending", "clean_title"),
        ("accidents", "No accidents reported by seller", "major_accident_free"),
        ("lemon", "No lemon details supplied", "lemon_free"),
    ],
)
def test_parse_cargurus_detail_keeps_ambiguous_structured_history_unknown(
    key, value, field
):
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    page = f"""
      <script type="application/json">
        {{"listing":{{"autoCheck":{{"{key}":"{value}"}}}}}}
      </script>
    """

    item = parse_cargurus_detail(page, seed)

    assert getattr(item, field) is None


def test_parse_cargurus_detail_keeps_missing_history_unknown():
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    item = parse_cargurus_detail("<html><body>Vehicle details</body></html>", seed)

    assert item.clean_title is None
    assert item.lemon_free is None
    assert item.major_accident_free is None
    assert item.title_evidence is None
    assert item.lemon_evidence is None
    assert item.accident_evidence is None


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return self.pages[url]


def test_run_cargurus_returns_complete_payload_with_exact_progress():
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    fetcher = FakeFetcher(
        {
            RESULTS_URL: fixture("cargurus-results.html"),
            seed.detail_url: fixture("cargurus-detail.html"),
        }
    )

    payload = run_cargurus(
        SpiderConfig(start_url=RESULTS_URL, max_pages=10, delay_seconds=0),
        fetcher=fetcher,
    )

    assert payload.status == RunStatus.COMPLETE
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 1
    assert payload.reported_total_pages == 1
    assert len(payload.observations) == 1
    assert fetcher.urls == [RESULTS_URL, seed.detail_url]


def test_run_cargurus_does_not_report_a_block_as_zero_complete_results():
    fetcher = FakeFetcher({RESULTS_URL: fixture("blocked.html")})

    payload = run_cargurus(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0), fetcher=fetcher
    )

    assert payload.status == RunStatus.BLOCKED
    assert payload.observations == ()
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 0


def test_run_cargurus_is_partial_when_reported_pages_remain_without_next_link():
    page = fixture("cargurus-results.html").replace("Page 1 of 1", "Page 1 of 3")
    seed = parse_cargurus_results(page, RESULTS_URL)[0]
    fetcher = FakeFetcher(
        {RESULTS_URL: page, seed.detail_url: fixture("cargurus-detail.html")}
    )

    payload = run_cargurus(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0), fetcher=fetcher
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 1
    assert payload.reported_total_pages == 3
    assert "next page" in payload.error_summary


def test_run_cargurus_is_partial_when_completed_pages_exceed_reported_total():
    first_page = fixture("cargurus-results.html").replace(
        "Page 1 of 1",
        'Page 1 of 1 <a rel="next" href="?page=2">Next</a>',
    )
    second_page = fixture("cargurus-results.html").replace("Page 1 of 1", "Page 2 of 1")
    second_url = f"{RESULTS_URL.split('?')[0]}?page=2"
    seed = parse_cargurus_results(first_page, RESULTS_URL)[0]
    fetcher = FakeFetcher(
        {
            RESULTS_URL: first_page,
            second_url: second_page,
            seed.detail_url: fixture("cargurus-detail.html"),
        }
    )

    payload = run_cargurus(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0), fetcher=fetcher
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.pages_attempted == 2
    assert payload.pages_completed == 2
    assert payload.reported_total_pages == 1
    assert "reported" in payload.error_summary


def test_run_cargurus_stops_at_internal_deadline_before_detail_fetch():
    seed = parse_cargurus_results(fixture("cargurus-results.html"), RESULTS_URL)[0]
    clock = iter((100.0, 106.0))
    fetcher = FakeFetcher(
        {
            RESULTS_URL: fixture("cargurus-results.html"),
            seed.detail_url: fixture("cargurus-detail.html"),
        }
    )

    payload = run_cargurus(
        SpiderConfig(
            start_url=RESULTS_URL,
            delay_seconds=0,
            deadline_monotonic=105.0,
        ),
        fetcher=fetcher,
        monotonic=lambda: next(clock),
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 1
    assert fetcher.urls == [RESULTS_URL]
    assert "deadline" in payload.error_summary


def test_run_cargurus_downgrades_local_quarantine_to_partial():
    results = fixture("cargurus-results.html")
    seed = parse_cargurus_results(results, RESULTS_URL)[0]
    malformed_detail = fixture("cargurus-detail.html").replace(
        "https://www.cargurus.com/images/CG-200-front.jpg",
        "https://images.example/CG-200-front.jpg",
    )

    payload = run_cargurus(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0),
        fetcher=FakeFetcher({RESULTS_URL: results, seed.detail_url: malformed_detail}),
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.quarantined_count == 1
    assert payload.observations == ()
    assert "quarantined" in payload.error_summary

