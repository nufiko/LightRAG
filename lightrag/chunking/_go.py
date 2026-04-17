"""Go chunking: split on func declarations."""

from __future__ import annotations

import re

EXTENSIONS = (".go",)

# Split before each top-level func declaration (including methods with receivers).
_BOUNDARY = re.compile(
    r"(?=\nfunc\s+)",
    re.MULTILINE,
)


def get_pattern(content: str) -> re.Pattern[str] | None:
    return _BOUNDARY
