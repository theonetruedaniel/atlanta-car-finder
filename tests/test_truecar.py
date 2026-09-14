from pathlib import Path

import pytest

from car_finder_scrapers.crawl import SpiderConfig
from car_finder_scrapers.models import RunStatus
from car_finder_scrapers.truecar import (
    parse_truecar_detail,
    parse_truecar_results,
    run_truecar,
)

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS_URL = "https://www.truecar.com/used-cars-for-sale/listings/location-atlanta-ga/"


def fixture(name):
    return (FIXTURES / name).read_text()


def test_parse_truecar_results_extracts_structured_listing_seed():
    seeds = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)

    assert len(seeds) == 1
    assert seeds[0].vin == "1HGCM82633A004352"
    assert seeds[0].listing_id == "TC-100"
    assert seeds[0].year == 2021
    assert (seeds[0].make, seeds[0].model, seeds[0].trim) == ("Honda", "Accord", "EX-L")
    assert seeds[0].list_price == 24_500
    assert seeds[0].mileage == 31_200
    assert seeds[0].exterior_color == "Crystal Black Pearl"
    assert seeds[0].detail_url == (
        "https://www.truecar.com/used-cars-for-sale/listing/1HGCM82633A004352/"
    )


def test_parse_truecar_results_supports_embedded_application_state():
    page = """
      <script type="application/json">
        {"props":{"inventory":[{"vin":"KNDJ23AU2N7158484","listingId":"TC-STATE",
        "url":"/used-cars-for-sale/listing/KNDJ23AU2N7158484/","year":2022,
        "make":"Kia","model":"Soul","trim":"EX","price":19000,"mileage":40000,
        "exteriorColor":"Black"}]}}
      </script>
    """

    seeds = parse_truecar_results(page, RESULTS_URL)

    assert len(seeds) == 1
    assert seeds[0].listing_id == "TC-STATE"
    assert seeds[0].list_price == 19_000
    assert seeds[0].mileage == 40_000


def test_parse_truecar_detail_extracts_only_affirmative_evidence():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    item = parse_truecar_detail(fixture("truecar-detail.html"), seed)

    assert item.vin == "1HGCM82633A004352"
    assert item.dealer_name == "Example Honda"
    assert item.dealer_kind == "franchise"
    assert item.body_style == "Sedan"
    assert item.transmission == "automatic"
    assert item.image_urls == (
        "https://www.truecar.com/images/TC-100-front.jpg",
        "https://www.truecar.com/images/TC-100-side.jpg",
    )
    assert item.canonical_url == seed.detail_url
    assert item.clean_title is True
    assert item.title_evidence == "Clean title status: yes"
    assert item.lemon_free is None
    assert item.lemon_evidence is None
    assert item.major_accident_free is True
    assert item.accident_evidence == "No accidents reported: true"
    assert item.carplay is True
    assert item.carplay_evidence == "Apple CarPlay: available"
    assert item.backup_camera is True
    assert item.backup_camera_evidence == "Backup Camera: yes"
    assert item.driver_assist == ("adaptive-cruise", "lane-keep")
    assert item.driver_assist_evidence == (
        "Adaptive Cruise Control: included; Lane Keep Assist: included"
    )
    assert item.required_evidence_conflict is False


def test_parse_truecar_detail_keeps_omitted_evidence_unknown():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    page = "<html><body><p>Great condition.</p></body></html>"

    item = parse_truecar_detail(page, seed)

    assert item.clean_title is None
    assert item.lemon_free is None
    assert item.major_accident_free is None
    assert item.carplay is None
    assert item.backup_camera is None
    assert item.driver_assist == ()
    assert item.title_evidence is None
    assert item.accident_evidence is None


def test_parse_truecar_detail_does_not_turn_negated_title_or_equipment_positive():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    page = """
      <html><body>
        <p>Not a clean title</p>
        <p>No Apple CarPlay</p>
        <p>No Backup Camera</p>
        <p>Adaptive Cruise Control not included</p>
      </body></html>
    """

    item = parse_truecar_detail(page, seed)

    assert item.clean_title is False
    assert item.title_evidence == "Not a clean title"
    assert item.carplay is False
    assert item.carplay_evidence == "No Apple CarPlay"
    assert item.backup_camera is False
    assert item.backup_camera_evidence == "No Backup Camera"
    assert item.driver_assist == ()
    assert item.driver_assist_evidence == "Adaptive Cruise Control not included"


def test_parse_truecar_detail_recognizes_postfixed_equipment_negation():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    item = parse_truecar_detail(
        "<html><body>Apple CarPlay unavailable</body></html>", seed
    )

    assert item.carplay is False
    assert item.carplay_evidence == "Apple CarPlay unavailable"


def test_parse_truecar_detail_recognizes_clause_and_value_negation():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    page = """
      <html><body>
        <p>This vehicle does not have Apple CarPlay.</p>
        <p>Clean title: No</p>
        <p>No support for Backup Camera</p>
        <p>Adaptive Cruise Control is excluded</p>
      </body></html>
    """

    item = parse_truecar_detail(page, seed)

    assert item.carplay is False
    assert item.carplay_evidence == "This vehicle does not have Apple CarPlay."
    assert item.clean_title is False
    assert item.title_evidence == "Clean title: No"
    assert item.backup_camera is False
    assert item.backup_camera_evidence == "No support for Backup Camera"
    assert item.driver_assist == ()
    assert item.driver_assist_evidence == "Adaptive Cruise Control is excluded"
    assert item.to_dict()["evidence"]["driverAssist"] == []
    assert (
        item.to_dict()["evidence"]["driverAssistDetail"]
        == "Adaptive Cruise Control is excluded"
    )


def test_parse_truecar_detail_preserves_explicit_negative_history():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    page = """
      <html><body>
        <p>Branded title reported</p>
        <p>One accident reported</p>
        <p>Lemon buyback</p>
      </body></html>
    """

    item = parse_truecar_detail(page, seed)

    assert item.clean_title is False
    assert item.title_evidence == "Branded title reported"
    assert item.major_accident_free is False
    assert item.accident_evidence == "One accident reported"
    assert item.lemon_free is False
    assert item.lemon_evidence == "Lemon buyback"


@pytest.mark.parametrize(
    ("statement", "field", "expected"),
    [
        ("Apple CarPlay: unavailable", "carplay", False),
        ("Apple CarPlay isn't available", "carplay", False),
        ("Vehicle lacks Backup Camera", "backup_camera", False),
        ("Clean title status: no", "clean_title", False),
        ("No accidents reported: false", "major_accident_free", False),
        ("Accident reported", "major_accident_free", False),
        ("Lemon law buyback", "lemon_free", False),
        ("Apple CarPlay: available", "carplay", True),
        ("Backup Camera: yes", "backup_camera", True),
        ("Clean title status: yes", "clean_title", True),
        ("No accidents reported: true", "major_accident_free", True),
    ],
)
def test_parse_truecar_detail_uses_bounded_evidence_statements(
    statement, field, expected
):
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    item = parse_truecar_detail(f"<html><body><p>{statement}</p></body></html>", seed)

    assert getattr(item, field) is expected
    assert statement in item.to_dict()["evidence"]["history"] or field in {
        "carplay",
        "backup_camera",
    }


@pytest.mark.parametrize(
    "statement",
    [
        "Adaptive Cruise Control is excluded",
        "Adaptive Cruise Control: unavailable",
        "Adaptive Cruise Control: not-equipped",
        "Not equipped with Adaptive Cruise Control",
        "Lane Keep Assist isn't available",
    ],
)
def test_parse_truecar_detail_serializes_negative_driver_assist_evidence(statement):
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    item = parse_truecar_detail(f"<html><body><p>{statement}</p></body></html>", seed)
    serialized = item.to_dict()["evidence"]

    assert item.driver_assist == ()
    assert item.driver_assist_evidence == statement
    assert serialized["driverAssist"] == []
    assert serialized["driverAssistDetail"] == statement


@pytest.mark.parametrize(
    ("statement", "field"),
    [
        ("Ask whether Apple CarPlay can be added", "carplay"),
        ("Backup Camera details vary by configuration", "backup_camera"),
        ("Clean title information is pending", "clean_title"),
        ("No accidents reported by some owners", "major_accident_free"),
    ],
)
def test_parse_truecar_detail_keeps_ambiguous_keyword_prose_unknown(statement, field):
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    item = parse_truecar_detail(f"<html><body><p>{statement}</p></body></html>", seed)

    assert getattr(item, field) is None


def test_parse_truecar_detail_accepts_only_structured_driver_assist_positive():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    page = """
      <html><body>
        <p>Adaptive Cruise Control might be available</p>
        <p>Lane Keep Assist: included</p>
      </body></html>
    """

    item = parse_truecar_detail(page, seed)

    assert item.driver_assist == ("lane-keep",)
    assert item.driver_assist_evidence == "Lane Keep Assist: included"


@pytest.mark.parametrize(
    ("markup", "field", "expected"),
    [
        ("Apple CarPlay <strong>unavailable</strong>", "carplay", False),
        ("Apple CarPlay <strong>isn't available</strong>", "carplay", False),
        (
            "Backup Camera <span>details vary by configuration</span>",
            "backup_camera",
            None,
        ),
    ],
)
def test_parse_truecar_detail_keeps_inline_markup_in_one_evidence_statement(
    markup, field, expected
):
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    page = (
        f"<html><body>{markup}</body></html>"
        if "isn't available" in markup
        else f"<html><body><p>{markup}</p></body></html>"
    )
    item = parse_truecar_detail(page, seed)

    assert getattr(item, field) is expected


@pytest.mark.parametrize(
    ("markup", "field", "expected", "evidence"),
    [
        (
            "<dl><dt>Apple CarPlay</dt><dd>Unavailable</dd></dl>",
            "carplay",
            False,
            "Apple CarPlay: Unavailable",
        ),
        (
            "<table><tr><th>Backup Camera</th><td>No</td></tr></table>",
            "backup_camera",
            False,
            "Backup Camera: No",
        ),
        (
            "<p>Clean title</p><p>No</p>",
            "clean_title",
            False,
            "Clean title: No",
        ),
        (
            (
                "<dl>\n <dt><span>Apple CarPlay</span></dt>\n"
                "<dd><strong>Available</strong></dd>\n</dl>"
            ),
            "carplay",
            True,
            "Apple CarPlay: Available",
        ),
        (
            (
                "<table><tr><th><span>Backup Camera</span></th>\n"
                "<td><em>Yes</em></td></tr></table>"
            ),
            "backup_camera",
            True,
            "Backup Camera: Yes",
        ),
        (
            "<div><p>Clean title</p>\n<p><strong>Yes</strong></p></div>",
            "clean_title",
            True,
            "Clean title: Yes",
        ),
        (
            "<section><h3>Backup Camera</h3>\n<p>No</p></section>",
            "backup_camera",
            False,
            "Backup Camera: No",
        ),
        (
            (
                "<div class='row'><div><span>Apple CarPlay</span></div>\n"
                "<div><strong>Unavailable</strong></div></div>"
            ),
            "carplay",
            False,
            "Apple CarPlay: Unavailable",
        ),
    ],
)
def test_parse_truecar_detail_pairs_adjacent_dom_label_values(
    markup, field, expected, evidence
):
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    item = parse_truecar_detail(f"<html><body>{markup}</body></html>", seed)

    assert getattr(item, field) is expected
    detail_field = {
        "carplay": "carplay_evidence",
        "backup_camera": "backup_camera_evidence",
        "clean_title": "title_evidence",
    }[field]
    assert getattr(item, detail_field) == evidence


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("Apple CarPlay", "carplay"),
        ("Backup Camera", "backup_camera"),
        ("Clean title", "clean_title"),
        ("Adaptive Cruise Control", "driver_assist"),
    ],
)
def test_parse_truecar_detail_keeps_isolated_labels_unknown(label, field):
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    item = parse_truecar_detail(f"<html><body><p>{label}</p></body></html>", seed)

    expected = () if field == "driver_assist" else None
    assert getattr(item, field) == expected


@pytest.mark.parametrize(
    ("statement", "field", "evidence_field"),
    [
        ("Title status: branded", "clean_title", "title_evidence"),
        ("Accident status: reported", "major_accident_free", "accident_evidence"),
        ("Manufacturer buyback reported", "lemon_free", "lemon_evidence"),
    ],
)
def test_parse_truecar_detail_preserves_structured_adverse_variants(
    statement, field, evidence_field
):
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    item = parse_truecar_detail(f"<html><body><p>{statement}</p></body></html>", seed)

    assert getattr(item, field) is False
    assert getattr(item, evidence_field) == statement
    assert statement in item.to_dict()["evidence"]["history"]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return self.pages[url]


def test_run_truecar_returns_complete_payload_with_exact_progress():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    fetcher = FakeFetcher(
        {
            RESULTS_URL: fixture("truecar-results.html"),
            seed.detail_url: fixture("truecar-detail.html"),
        }
    )
    config = SpiderConfig(start_url=RESULTS_URL, max_pages=10, delay_seconds=0)

    payload = run_truecar(config, fetcher=fetcher)

    assert payload.status == RunStatus.COMPLETE
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 1
    assert payload.reported_total_pages == 1
    assert len(payload.observations) == 1
    assert fetcher.urls == [RESULTS_URL, seed.detail_url]


def test_run_truecar_records_a_first_page_block_as_blocked():
    fetcher = FakeFetcher({RESULTS_URL: fixture("blocked.html")})

    payload = run_truecar(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0), fetcher=fetcher
    )

    assert payload.status == RunStatus.BLOCKED
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 0
    assert payload.observations == ()
    assert "blocked" in payload.error_summary.casefold()


def test_run_truecar_marks_unrecognized_nonempty_results_partial():
    fetcher = FakeFetcher({RESULTS_URL: "<html><body>Inventory loaded</body></html>"})

    payload = run_truecar(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0), fetcher=fetcher
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 1
    assert "no listing seeds" in payload.error_summary


def test_run_truecar_marks_a_detail_transport_failure_partial():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]

    class DetailFailureFetcher(FakeFetcher):
        def fetch(self, url):
            if url == seed.detail_url:
                raise ConnectionError("detail interrupted")
            return super().fetch(url)

    payload = run_truecar(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0),
        fetcher=DetailFailureFetcher({RESULTS_URL: fixture("truecar-results.html")}),
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 1
    assert payload.quarantined_count == 0


def test_run_truecar_is_partial_when_reported_pages_remain_without_next_link():
    page = fixture("truecar-results.html").replace("Page 1 of 1", "Page 1 of 3")
    seed = parse_truecar_results(page, RESULTS_URL)[0]
    fetcher = FakeFetcher(
        {RESULTS_URL: page, seed.detail_url: fixture("truecar-detail.html")}
    )

    payload = run_truecar(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0), fetcher=fetcher
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.pages_attempted == 1
    assert payload.pages_completed == 1
    assert payload.reported_total_pages == 3
    assert "next page" in payload.error_summary


def test_run_truecar_is_partial_when_reported_total_changes_between_pages():
    first_page = fixture("truecar-results.html").replace(
        "Page 1 of 1",
        'Page 1 of 2 <a rel="next" href="?page=2">Next</a>',
    )
    second_page = fixture("truecar-results.html").replace("Page 1 of 1", "Page 2 of 3")
    second_url = f"{RESULTS_URL}?page=2"
    seed = parse_truecar_results(first_page, RESULTS_URL)[0]
    fetcher = FakeFetcher(
        {
            RESULTS_URL: first_page,
            second_url: second_page,
            seed.detail_url: fixture("truecar-detail.html"),
        }
    )

    payload = run_truecar(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0), fetcher=fetcher
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.pages_attempted == 2
    assert payload.pages_completed == 2
    assert payload.reported_total_pages == 3
    assert "reported" in payload.error_summary


def test_run_truecar_stops_at_internal_deadline_before_detail_fetch():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    clock = iter((100.0, 106.0))
    fetcher = FakeFetcher(
        {
            RESULTS_URL: fixture("truecar-results.html"),
            seed.detail_url: fixture("truecar-detail.html"),
        }
    )

    payload = run_truecar(
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


def test_run_truecar_marks_partial_when_deadline_expires_during_detail_fetch():
    seed = parse_truecar_results(fixture("truecar-results.html"), RESULTS_URL)[0]
    clock = iter((100.0, 101.0, 102.0, 106.0))
    fetcher = FakeFetcher(
        {
            RESULTS_URL: fixture("truecar-results.html"),
            seed.detail_url: fixture("truecar-detail.html"),
        }
    )

    payload = run_truecar(
        SpiderConfig(
            start_url=RESULTS_URL,
            delay_seconds=0,
            deadline_monotonic=105.0,
        ),
        fetcher=fetcher,
        monotonic=lambda: next(clock),
    )

    assert payload.status == RunStatus.PARTIAL
    assert len(payload.observations) == 1
    assert "deadline" in payload.error_summary


def test_run_truecar_downgrades_local_quarantine_to_partial():
    results = fixture("truecar-results.html")
    seed = parse_truecar_results(results, RESULTS_URL)[0]
    malformed_detail = fixture("truecar-detail.html").replace(
        "https://www.truecar.com/images/TC-100-front.jpg",
        "https://images.example/TC-100-front.jpg",
    )

    payload = run_truecar(
        SpiderConfig(start_url=RESULTS_URL, delay_seconds=0),
        fetcher=FakeFetcher({RESULTS_URL: results, seed.detail_url: malformed_detail}),
    )

    assert payload.status == RunStatus.PARTIAL
    assert payload.quarantined_count == 1
    assert payload.observations == ()
    assert "quarantined" in payload.error_summary

