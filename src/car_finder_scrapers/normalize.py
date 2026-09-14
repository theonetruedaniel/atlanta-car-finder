from __future__ import annotations

import re
from datetime import UTC, datetime

VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
INTEGER_PATTERN = re.compile(r"\d[\d,]*")


def normalize_vin(value: str) -> str:
    normalized = value.strip().upper()
    if not VIN_PATTERN.fullmatch(normalized):
        raise ValueError("VIN must contain 17 valid characters")
    return normalized


def parse_integer(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("integer value cannot be boolean")  # noqa: TRY004
    if isinstance(value, int):
        return value
    match = INTEGER_PATTERN.search(value)
    if match is None:
        raise ValueError(f"could not parse integer from {value!r}")
    return int(match.group(0).replace(",", ""))


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

