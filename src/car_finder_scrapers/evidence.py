from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from itertools import pairwise

from .crawl import page_html

POSITIVE_VALUES = r"(?:yes|true|available|included|equipped|standard)"
NEGATIVE_VALUES = (
    r"(?:no|false|unavailable|excluded|absent|not\s+available|not\s+included|"
    r"not[-\s]+equipped|not\s+supported)"
)


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    value: bool | None
    evidence: str | None
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class _Block:
    tag: str
    text: str
    parent_id: int


@dataclass(slots=True)
class _BlockFrame:
    tag: str
    parent_id: int
    parts: list[str]
    has_block_child: bool = False


class _EvidenceHtmlParser(HTMLParser):
    _BLOCK_TAGS = frozenset(
        {"div", "p", "li", "dt", "dd", "td", "th", "h1", "h2", "h3", "h4"}
    )
    _HIDDEN_TAGS = frozenset({"script", "style", "template", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.blocks: list[_Block] = []
        self.loose_statements: list[str] = []
        self._element_stack: list[tuple[str, int]] = []
        self._next_element_id = 1
        self._block_stack: list[_BlockFrame] = []
        self._loose_parts: list[str] = []

    def _flush_loose_text(self) -> None:
        statement = _normalize(" ".join(self._loose_parts))
        if statement:
            self.loose_statements.append(statement)
        self._loose_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent_id = self._element_stack[-1][1] if self._element_stack else 0
        element_id = self._next_element_id
        self._next_element_id += 1
        self._element_stack.append((tag, element_id))
        if tag in self._HIDDEN_TAGS:
            if not self._block_stack:
                self._flush_loose_text()
            self.hidden_depth += 1
        elif tag in self._BLOCK_TAGS:
            if not self._block_stack:
                self._flush_loose_text()
            else:
                self._block_stack[-1].has_block_child = True
            self._block_stack.append(_BlockFrame(tag, parent_id, []))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._HIDDEN_TAGS and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag in self._BLOCK_TAGS and self._block_stack:
            frame = self._block_stack.pop()
            statement = _normalize(" ".join(frame.parts))
            if statement and not frame.has_block_child:
                self.blocks.append(_Block(frame.tag, statement, frame.parent_id))
        if self._element_stack:
            self._element_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.hidden_depth:
            return
        if self._block_stack:
            for frame in self._block_stack:
                frame.parts.append(data)
        else:
            self._loose_parts.append(data)

    def finish(self) -> None:
        self._flush_loose_text()


def _normalize(value: object) -> str:
    return " ".join(str(value or "").split())


def _matches(value: str, pattern: str) -> bool:
    return bool(re.fullmatch(rf"(?:{pattern})[.!]?", value, flags=re.IGNORECASE))


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    statements: tuple[str, ...]
    pairs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_html(cls, page: object) -> EvidenceContext:
        parser = _EvidenceHtmlParser()
        parser.feed(page_html(page))
        parser.close()
        parser.finish()
        blocks = parser.blocks
        pairs = tuple(
            (left.text, right.text)
            for left, right in pairwise(blocks)
            if left.parent_id == right.parent_id
        )
        return cls(
            statements=tuple(block.text for block in blocks)
            + tuple(parser.loose_statements),
            pairs=pairs,
        )

    @classmethod
    def from_values(cls, values: tuple[object, ...] | list[object]) -> EvidenceContext:
        return cls(tuple(value for raw in values if (value := _normalize(raw))))

    def first(self, patterns: tuple[str, ...]) -> str | None:
        for statement in self.statements:
            if any(_matches(statement, pattern) for pattern in patterns):
                return statement
        return None

    def claim(
        self,
        label: str,
        *,
        allow_exact_label: bool = False,
        positive_patterns: tuple[str, ...] = (),
        negative_patterns: tuple[str, ...] = (),
    ) -> EvidenceClaim:
        escaped = re.escape(label)
        label_pattern = rf"{escaped}(?:\s+status)?"
        pair_positive = self._pair(label_pattern, POSITIVE_VALUES)
        pair_negative = self._pair(label_pattern, NEGATIVE_VALUES)
        statement_positive = self.first(
            (
                *((escaped,) if allow_exact_label else ()),
                rf"{label_pattern}\s*[:=-]\s*{POSITIVE_VALUES}",
                rf"{escaped}\s+(?:is\s+)?(?:available|included|equipped|standard)",
                *positive_patterns,
            )
        )
        statement_negative = self.first(
            (
                rf"{label_pattern}\s*[:=-]\s*{NEGATIVE_VALUES}",
                rf"{escaped}\s+(?:(?:is|was)\s+)?{NEGATIVE_VALUES}",
                (
                    rf"{escaped}\s+(?:isn't|wasn't)\s+"
                    r"(?:available|included|equipped|supported)"
                ),
                (
                    rf"(?:no|without|no\s+support\s+for|not\s+equipped\s+with|"
                    rf"not\s+(?:a|an))\s+{escaped}"
                ),
                (
                    rf"(?:this\s+)?vehicle\s+"
                    rf"(?:lacks|doesn't\s+have|does\s+not\s+have)\s+{escaped}"
                ),
                *negative_patterns,
            )
        )
        positive = pair_positive or statement_positive
        negative = pair_negative or statement_negative
        if negative is not None:
            return EvidenceClaim(False, negative, positive is not None)
        if positive is not None:
            return EvidenceClaim(True, positive)
        return EvidenceClaim(None, None)

    def _pair(self, label_pattern: str, value_pattern: str) -> str | None:
        for label, value in self.pairs:
            if _matches(label, label_pattern) and _matches(value, value_pattern):
                return f"{label}: {value}"
        return None

