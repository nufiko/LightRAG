"""PowerShell chunking: split on function declarations."""

from __future__ import annotations

import re

EXTENSIONS = (".ps1",)

_BOUNDARY = re.compile(r"(?=\nfunction\s+)", re.MULTILINE | re.IGNORECASE)


def get_pattern(content: str) -> re.Pattern[str] | None:
    return _BOUNDARY
