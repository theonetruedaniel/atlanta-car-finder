from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping
from typing import TextIO

from .cargurus import run_cargurus
from .crawl import SpiderConfig
from .models import RunPayload, RunStatus
from .normalize import utc_now_iso
from .truecar import run_truecar
from .upload import UploadResult, upload_run

TRUECAR_SEARCH_URL = (
    "https://www.truecar.com/used-cars-for-sale/listings/location-marietta-ga/"
    "?searchRadius=75&startYear=2017&priceHigh=11495&transmission[]=AUTOMATIC"
)
CARGURUS_SEARCH_URL = (
    "https://www.cargurus.com/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action"
    "?zip=30067&distance=75&minYear=2017&maxPrice=11495&transmission=AUTOMATIC"
)

Runner = Callable[[SpiderConfig], RunPayload]
Uploader = Callable[[RunPayload, str, str], UploadResult]
SOURCE_CRAWL_BUDGET_SECONDS = 10 * 60


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="car-finder-scrapers")
    commands = parser.add_subparsers(dest="command", required=True)
    crawl = commands.add_parser("crawl")
    crawl.add_argument("source", choices=("truecar", "cargurus", "all"))
    return parser


def _failed_payload(source_key: str, exc: Exception) -> RunPayload:
    source = "TrueCar" if source_key == "truecar" else "CarGurus"
    timestamp = utc_now_iso()
    return RunPayload(
        source=source,
        run_id=f"{source_key}-{timestamp.replace('-', '').replace(':', '')}",
        started_at=timestamp,
        finished_at=timestamp,
        pages_attempted=0,
        pages_completed=0,
        reported_total_pages=None,
        status=RunStatus.FAILED,
        error_summary=f"runner failed: {type(exc).__name__}",
        observations=(),
        quarantined_count=0,
    )


def main(
    argv: list[str] | None = None,
    *,
    runners: Mapping[str, Runner] | None = None,
    uploader: Uploader = upload_run,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    output = sys.stdout if out is None else out
    endpoint = environment.get("DASHBOARD_INGESTION_URL")
    secret = environment.get("SCRAPLING_INGESTION_SECRET")
    if not endpoint or not secret:
        print(
            "Missing DASHBOARD_INGESTION_URL or SCRAPLING_INGESTION_SECRET.",
            file=output,
        )
        return 2

    configured_runners: Mapping[str, Runner] = runners or {
        "truecar": run_truecar,
        "cargurus": run_cargurus,
    }
    selected = ("truecar", "cargurus") if args.source == "all" else (args.source,)
    urls = {"truecar": TRUECAR_SEARCH_URL, "cargurus": CARGURUS_SEARCH_URL}
    exit_code = 0

    for source_key in selected:
        runner_failed = False
        try:
            payload = configured_runners[source_key](
                SpiderConfig(
                    start_url=urls[source_key],
                    deadline_monotonic=monotonic() + SOURCE_CRAWL_BUDGET_SECONDS,
                )
            )
        except Exception as exc:  # noqa: BLE001 - top-level runner failures must still upload telemetry
            runner_failed = True
            payload = _failed_payload(source_key, exc)
        try:
            result = uploader(payload, endpoint, secret)
        except Exception as exc:  # noqa: BLE001 - one source upload must not starve the next
            result = UploadResult(
                success=False,
                status_code=None,
                attempts=0,
                message=f"upload exception: {type(exc).__name__}",
            )
        upload_state = "accepted" if result.success else "failed"
        print(
            f"{payload.source}: status={payload.status.value} "
            f"pages={payload.pages_completed}/{payload.pages_attempted} "
            f"observations={len(payload.observations)} quarantined={payload.quarantined_count} "
            f"upload={upload_state} attempts={result.attempts} "
            f"detail={result.message}",
            file=output,
        )
        if runner_failed or not result.success:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

