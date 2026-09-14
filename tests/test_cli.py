from io import StringIO
from urllib.parse import parse_qs, urlparse

from car_finder_scrapers.cli import main
from car_finder_scrapers.models import RunPayload, RunStatus
from car_finder_scrapers.upload import UploadResult


def run_payload(source, status):
    return RunPayload(
        source=source,
        run_id=f"{source.lower()}-test",
        started_at="2026-08-20T10:00:00Z",
        finished_at="2026-08-20T10:01:00Z",
        pages_attempted=1,
        pages_completed=0 if status == RunStatus.BLOCKED else 1,
        reported_total_pages=None,
        status=status,
        error_summary="blocked" if status == RunStatus.BLOCKED else None,
        observations=(),
        quarantined_count=0,
    )


def test_crawl_all_uploads_each_source_independently_including_blocked():
    uploaded = []
    runners = {
        "truecar": lambda config: run_payload("TrueCar", RunStatus.BLOCKED),
        "cargurus": lambda config: run_payload("CarGurus", RunStatus.COMPLETE),
    }

    def uploader(payload, endpoint, secret):
        uploaded.append((payload.source, endpoint, secret))
        return UploadResult(
            success=True, status_code=202, attempts=1, message="accepted"
        )

    output = StringIO()
    exit_code = main(
        ["crawl", "all"],
        runners=runners,
        uploader=uploader,
        environ={
            "DASHBOARD_INGESTION_URL": "https://dashboard.example/ingest",
            "SCRAPLING_INGESTION_SECRET": "secret",
        },
        out=output,
    )

    assert exit_code == 0
    assert [source for source, _, _ in uploaded] == ["TrueCar", "CarGurus"]
    assert "TrueCar: status=blocked" in output.getvalue()
    assert "CarGurus: status=complete" in output.getvalue()


def test_crawl_source_returns_nonzero_when_upload_fails_without_printing_secret():
    secret = "must-not-leak"

    def uploader(payload, endpoint, supplied_secret):
        return UploadResult(
            success=False, status_code=503, attempts=3, message="server error"
        )

    output = StringIO()
    exit_code = main(
        ["crawl", "truecar"],
        runners={"truecar": lambda config: run_payload("TrueCar", RunStatus.COMPLETE)},
        uploader=uploader,
        environ={
            "DASHBOARD_INGESTION_URL": "https://dashboard.example/ingest",
            "SCRAPLING_INGESTION_SECRET": secret,
        },
        out=output,
    )

    assert exit_code == 1
    assert "upload=failed" in output.getvalue()
    assert "detail=server error" in output.getvalue()
    assert secret not in output.getvalue()


def test_crawl_all_continues_after_one_uploader_raises():
    ran = []
    uploaded = []

    def runner(source, status):
        def run(config):
            ran.append(source)
            return run_payload(source, status)

        return run

    def uploader(payload, endpoint, secret):
        uploaded.append(payload.source)
        if payload.source == "TrueCar":
            raise RuntimeError("client exploded")
        return UploadResult(True, 202, 1, "accepted")

    output = StringIO()
    exit_code = main(
        ["crawl", "all"],
        runners={
            "truecar": runner("TrueCar", RunStatus.COMPLETE),
            "cargurus": runner("CarGurus", RunStatus.COMPLETE),
        },
        uploader=uploader,
        environ={
            "DASHBOARD_INGESTION_URL": "https://dashboard.example/ingest",
            "SCRAPLING_INGESTION_SECRET": "must-not-leak",
        },
        out=output,
    )

    assert exit_code == 1
    assert ran == ["TrueCar", "CarGurus"]
    assert uploaded == ["TrueCar", "CarGurus"]
    assert "TrueCar: status=complete" in output.getvalue()
    assert "upload=failed" in output.getvalue()
    assert "detail=upload exception: RuntimeError" in output.getvalue()
    assert "must-not-leak" not in output.getvalue()


def test_cli_rejects_missing_ingestion_configuration():
    output = StringIO()
    exit_code = main(["crawl", "cargurus"], environ={}, out=output)

    assert exit_code == 2
    assert "DASHBOARD_INGESTION_URL" in output.getvalue()
    assert "SCRAPLING_INGESTION_SECRET" in output.getvalue()


def test_cli_uploads_failed_telemetry_when_a_runner_crashes():
    uploaded = []

    def broken_runner(config):
        raise RuntimeError("browser unavailable")

    def uploader(payload, endpoint, secret):
        uploaded.append(payload)
        return UploadResult(
            success=True, status_code=202, attempts=1, message="accepted"
        )

    exit_code = main(
        ["crawl", "truecar"],
        runners={"truecar": broken_runner},
        uploader=uploader,
        environ={
            "DASHBOARD_INGESTION_URL": "https://dashboard.example/ingest",
            "SCRAPLING_INGESTION_SECRET": "secret",
        },
        out=StringIO(),
    )

    assert exit_code == 1
    assert len(uploaded) == 1
    assert uploaded[0].status == RunStatus.FAILED
    assert uploaded[0].pages_attempted == 0
    assert uploaded[0].error_summary == "runner failed: RuntimeError"


def test_default_searches_preserve_the_approved_atlanta_criteria():
    configured_urls = {}

    def runner(source):
        def run(config):
            configured_urls[source] = config.start_url
            return run_payload(
                "TrueCar" if source == "truecar" else "CarGurus", RunStatus.COMPLETE
            )

        return run

    main(
        ["crawl", "all"],
        runners={"truecar": runner("truecar"), "cargurus": runner("cargurus")},
        uploader=lambda payload, endpoint, secret: UploadResult(
            True, 202, 1, "accepted"
        ),
        environ={
            "DASHBOARD_INGESTION_URL": "https://dashboard.example/ingest",
            "SCRAPLING_INGESTION_SECRET": "secret",
        },
        out=StringIO(),
    )

    truecar = parse_qs(urlparse(configured_urls["truecar"]).query)
    cargurus = parse_qs(urlparse(configured_urls["cargurus"]).query)
    assert truecar["searchRadius"] == ["75"]
    assert truecar["startYear"] == ["2017"]
    assert truecar["priceHigh"] == ["11495"]
    assert truecar["transmission[]"] == ["AUTOMATIC"]
    assert "/location-marietta-ga/" in configured_urls["truecar"]
    assert cargurus["zip"] == ["30067"]
    assert cargurus["distance"] == ["75"]
    assert cargurus["minYear"] == ["2017"]
    assert cargurus["maxPrice"] == ["11495"]
    assert cargurus["transmission"] == ["AUTOMATIC"]


def test_crawl_all_assigns_each_source_a_bounded_internal_deadline():
    configs = []

    def runner(source):
        def run(config):
            configs.append(config)
            return run_payload(source, RunStatus.COMPLETE)

        return run

    exit_code = main(
        ["crawl", "all"],
        runners={"truecar": runner("TrueCar"), "cargurus": runner("CarGurus")},
        uploader=lambda payload, endpoint, secret: UploadResult(
            True, 202, 1, "accepted"
        ),
        environ={
            "DASHBOARD_INGESTION_URL": "https://dashboard.example/ingest",
            "SCRAPLING_INGESTION_SECRET": "secret",
        },
        out=StringIO(),
        monotonic=lambda: 1_000.0,
    )

    assert exit_code == 0
    assert [config.deadline_monotonic for config in configs] == [1_600.0, 1_600.0]

