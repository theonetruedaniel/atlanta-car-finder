from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urldefrag, urljoin

BLOCK_MARKERS = (
    "access denied",
    "verify you are human",
    "checking your browser",
    "unusual traffic",
    "complete the challenge",
    "captcha",
)
EMPTY_MARKERS = (
    "no vehicles found",
    "no cars found",
    "0 results",
    "zero results",
)


def page_html(page: object) -> str:
    if isinstance(page, str):
        return page
    for attribute in ("html_content", "body", "text"):
        value = getattr(page, attribute, None)
        if callable(value):
            value = value()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            return value
    raise TypeError("page must be HTML text or expose html_content, body, or text")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template", "noscript"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template", "noscript"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[dict[str, str], str]] = []
        self._attrs: dict[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._attrs = {key.lower(): value or "" for key, value in attrs}
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._attrs is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._attrs is not None:
            self.links.append((self._attrs, " ".join(self._text).strip()))
            self._attrs = None
            self._text = []


def visible_text(page: object) -> str:
    parser = _VisibleTextParser()
    parser.feed(page_html(page))
    return " ".join(" ".join(parser.parts).split())


def classify_page(page: object) -> Literal["ok", "blocked", "empty"]:
    status = getattr(page, "status", None)
    if isinstance(status, int) and status in {401, 403, 429}:
        return "blocked"
    text = visible_text(page).casefold()
    if any(marker in text for marker in BLOCK_MARKERS):
        return "blocked"
    if any(marker in text for marker in EMPTY_MARKERS):
        return "empty"
    return "ok"


def next_page_url(page: object, current_url: str) -> str | None:
    parser = _LinkParser()
    parser.feed(page_html(page))
    for attributes, text in parser.links:
        rel = attributes.get("rel", "").casefold().split()
        label = attributes.get("aria-label", "").casefold()
        is_next = (
            "next" in rel or "next" in label or text.casefold() in {"next", "next page"}
        )
        disabled = (
            attributes.get("aria-disabled", "").casefold() == "true"
            or "disabled" in attributes
        )
        href = attributes.get("href")
        if is_next and not disabled and href:
            return urljoin(current_url, href)
    return None


@dataclass(frozen=True, slots=True)
class SpiderConfig:
    start_url: str
    max_pages: int = 50
    delay_seconds: float = 1.5
    request_timeout_ms: int = 30_000
    deadline_monotonic: float | None = None

    def __post_init__(self) -> None:
        if self.max_pages < 1:
            raise ValueError("max_pages must be at least one")
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")


@dataclass(slots=True)
class CrawlTelemetry:
    max_pages: int
    reported_total_pages: int | None = None
    pages_attempted: int = 0
    pages_completed: int = 0
    limit_reached: bool = False
    _visited: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if self.max_pages < 1:
            raise ValueError("max_pages must be at least one")

    def begin_page(self, url: str) -> bool:
        normalized_url, _ = urldefrag(url)
        if normalized_url in self._visited:
            return False
        if len(self._visited) >= self.max_pages:
            self.limit_reached = True
            return False
        self._visited.add(normalized_url)
        self.pages_attempted += 1
        return True

    def complete_page(self) -> None:
        if self.pages_completed >= self.pages_attempted:
            raise ValueError("cannot complete a page that was not attempted")
        self.pages_completed += 1

