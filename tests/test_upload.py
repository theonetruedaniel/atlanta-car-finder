import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from car_finder_scrapers.models import Observation, RunPayload, RunStatus
from car_finder_scrapers.upload import SameOriginRedirectHandler, upload_run


def payload():
    item = Observation(
        source="TrueCar",
        listing_id="TC-1",
        vin="1HGCM82633A004352",
        year=2021,
        make="Honda",
        model="Accord",
        trim="EX-L",
        body_style="Sedan",
        transmission="automatic",
        list_price=24_500,
        mileage=31_200,
        distance_miles=None,
        local_delivery=False,
        exterior_color="Black",
        dealer_name="Example Honda",
        dealer_kind="franchise",
        dealer_accepted=None,
        dealer_rating=None,
        dealer_review_count=None,
        image_urls=(),
        canonical_url="https://www.truecar.com/listing/TC-1",
        clean_title=None,
        title_evidence=None,
        lemon_free=None,
        lemon_evidence=None,
        major_accident_free=None,
        accident_evidence=None,
        carplay=None,
        carplay_evidence=None,
        backup_camera=None,
        backup_camera_evidence=None,
        driver_assist=(),
        driver_assist_evidence=None,
        required_evidence_conflict=False,
        fee_evidence=None,
    )
    return RunPayload(
        source="TrueCar",
        run_id="truecar-test",
        started_at="2026-08-20T10:00:00Z",
        finished_at="2026-08-20T10:01:00Z",
        pages_attempted=1,
        pages_completed=1,
        reported_total_pages=1,
        status=RunStatus.COMPLETE,
        error_summary=None,
        observations=(item,),
        quarantined_count=0,
    )


@contextmanager
def upload_server(statuses):
    state = {"statuses": list(statuses), "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            state["requests"].append({"headers": dict(self.headers), "body": body})
            status = state["statuses"].pop(0)
            if status is None:
                self.connection.close()
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"accepted": status < 400}).encode())

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/ingestion", state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def loopback_opener(endpoint):
    def open_request(request, timeout):
        forwarded = Request(
            endpoint,
            data=request.data,
            headers=dict(request.header_items()),
            method=request.method,
        )
        return urlopen(forwarded, timeout=timeout)

    return open_request


def test_upload_sends_bearer_json_and_contract_payload():
    with upload_server([202]) as (endpoint, state):
        result = upload_run(
            payload(),
            "https://dashboard.example/ingestion",
            "top-secret",
            backoff_seconds=0,
            opener=loopback_opener(endpoint),
        )

    request = state["requests"][0]
    assert result.success is True
    assert result.status_code == 202
    assert request["headers"]["Authorization"] == "Bearer top-secret"
    assert request["headers"]["Content-Type"] == "application/json"
    assert json.loads(request["body"])["runId"] == "truecar-test"


def test_upload_retries_a_5xx_response():
    with upload_server([503, 202]) as (endpoint, state):
        result = upload_run(
            payload(),
            "https://dashboard.example/ingestion",
            "secret",
            max_attempts=3,
            backoff_seconds=0,
            opener=loopback_opener(endpoint),
        )

    assert result.success is True
    assert result.attempts == 2
    assert len(state["requests"]) == 2


def test_upload_retries_a_transport_disconnect():
    with upload_server([None, 202]) as (endpoint, state):
        result = upload_run(
            payload(),
            "https://dashboard.example/ingestion",
            "secret",
            max_attempts=3,
            backoff_seconds=0,
            opener=loopback_opener(endpoint),
        )

    assert result.success is True
    assert result.attempts == 2
    assert len(state["requests"]) == 2


def test_upload_does_not_retry_a_4xx_response():
    with upload_server([400, 202]) as (endpoint, state):
        result = upload_run(
            payload(),
            "https://dashboard.example/ingestion",
            "secret",
            max_attempts=3,
            backoff_seconds=0,
            opener=loopback_opener(endpoint),
        )

    assert result.success is False
    assert result.status_code == 400
    assert result.attempts == 1
    assert len(state["requests"]) == 1


def test_upload_enforces_a_bounded_timeout():
    try:
        upload_run(payload(), "https://example.com", "secret", timeout_seconds=31)
    except ValueError as exc:
        assert "timeout" in str(exc)
    else:
        raise AssertionError("timeout above the safety bound was accepted")


def test_upload_passes_timeout_to_urllib_as_a_keyword():
    observed = {}
    with upload_server([202]) as (endpoint, _state):
        forward = loopback_opener(endpoint)

        def keyword_only_opener(request, *, timeout):
            observed["timeout"] = timeout
            return forward(request, timeout)

        result = upload_run(
            payload(),
            "https://dashboard.example/ingestion",
            "secret",
            timeout_seconds=7,
            backoff_seconds=0,
            opener=keyword_only_opener,
        )

    assert result.success is True
    assert observed["timeout"] == 7


def test_upload_rejects_a_non_https_endpoint_before_transport():
    try:
        upload_run(payload(), "http://dashboard.example/ingestion", "secret")
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("non-HTTPS endpoint was accepted")


def test_redirect_policy_rejects_cross_origin_authorization_forwarding():
    handler = SameOriginRedirectHandler()
    request = Request("https://dashboard.example/ingestion", method="POST")

    try:
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://other.example/ingestion",
        )
    except HTTPError as exc:
        assert exc.code == 302
    else:
        raise AssertionError("cross-origin redirect was accepted")

