from pathlib import Path

import pytest

from car_finder_scrapers.crawl import CrawlTelemetry, classify_page, next_page_url

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text()


def test_classify_page_recognizes_access_denied_visible_text():
    assert classify_page(fixture("blocked.html")) == "blocked"


@pytest.mark.parametrize(
    "message",
    [
        "Just a moment... Checking your browser",
        "Unusual traffic detected. Complete the challenge",
    ],
)
def test_classify_page_recognizes_challenge_text(message):
    assert classify_page(f"<html><body>{message}</body></html>") == "blocked"


def test_classify_page_does_not_treat_script_text_as_a_block():
    page = "<html><script>const message = 'access denied';</script><body>Inventory loaded</body></html>"
    assert classify_page(page) == "ok"


@pytest.mark.parametrize("status", [401, 403, 429])
def test_classify_page_recognizes_blocking_http_status(status):
    class Response:
        html_content = "<html><body>Inventory loaded</body></html>"

        def __init__(self, response_status):
            self.status = response_status

    assert classify_page(Response(status)) == "blocked"


def test_classify_page_keeps_successful_http_response_text_based():
    class Response:
        status = 200
        html_content = "<html><body>Inventory loaded</body></html>"

    assert classify_page(Response()) == "ok"


def test_classify_page_recognizes_normal_empty_inventory():
    assert (
        classify_page(
            "<main><h2>No vehicles found</h2><p>Try changing your filters.</p></main>"
        )
        == "empty"
    )


def test_next_page_url_resolves_relative_next_link():
    assert next_page_url(
        fixture("paginated.html"), "https://www.truecar.com/used-cars?page=1"
    ) == ("https://www.truecar.com/used-cars?page=2")


def test_next_page_url_preserves_absolute_next_link():
    page = '<a aria-label="Next page" href="https://www.cargurus.com/search?page=4">Next</a>'
    assert next_page_url(page, "https://www.cargurus.com/search?page=3") == (
        "https://www.cargurus.com/search?page=4"
    )


def test_next_page_url_ignores_disabled_next_link():
    page = '<a rel="next" aria-disabled="true" href="?page=5">Next</a>'
    assert next_page_url(page, "https://www.truecar.com/search?page=4") is None


def test_telemetry_rejects_a_repeated_url_loop():
    telemetry = CrawlTelemetry(max_pages=10)
    assert telemetry.begin_page("https://www.truecar.com/search?page=1") is True
    telemetry.complete_page()
    assert telemetry.begin_page("https://www.truecar.com/search?page=1") is False
    assert telemetry.pages_attempted == 1


def test_telemetry_enforces_page_safety_limit():
    telemetry = CrawlTelemetry(max_pages=2)
    assert telemetry.begin_page("https://www.truecar.com/search?page=1") is True
    assert telemetry.begin_page("https://www.truecar.com/search?page=2") is True
    assert telemetry.begin_page("https://www.truecar.com/search?page=3") is False
    assert telemetry.limit_reached is True


def test_telemetry_tracks_exact_progress():
    telemetry = CrawlTelemetry(max_pages=5, reported_total_pages=4)
    telemetry.begin_page("https://www.truecar.com/search?page=1")
    telemetry.complete_page()
    telemetry.begin_page("https://www.truecar.com/search?page=2")

    assert telemetry.pages_attempted == 2
    assert telemetry.pages_completed == 1
    assert telemetry.reported_total_pages == 4

