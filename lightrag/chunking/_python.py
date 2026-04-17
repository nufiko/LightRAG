"""Python chunking: split on top-level def/class boundaries."""

from __future__ import annotations

import re

EXTENSIONS = (".py",)

# Split before any top-level (unindented) def, async def, or class declaration.
_BOUNDARY = re.compile(
    r"(?=\n(?:def |async def |class )\w)",
    re.MULTILINE,
)


def get_pattern(content: str) -> re.Pattern[str] | None:
    return _BOUNDARY
