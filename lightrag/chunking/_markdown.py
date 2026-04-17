"""Markdown chunking: split on headings."""

from __future__ import annotations

import re

EXTENSIONS = (".md",)

_BOUNDARY = re.compile(r"(?=\n#{1,3} )", re.MULTILINE)


def get_pattern(content: str) -> re.Pattern[str] | None:
    return _BOUNDARY
