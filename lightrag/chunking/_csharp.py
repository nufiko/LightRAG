"""C# chunking: split on member boundaries."""

from __future__ import annotations

import re

EXTENSIONS = (".cs", ".razor", ".cshtml")

# Blank line before an optional run of attributes then an access/modifier keyword.
_BOUNDARY = re.compile(
    r"(?=\n[ \t]*\n"
    r"(?:[ \t]*\[[^\]\n]+\][ \t]*\n)*"
    r"[ \t]+"
    r"(?:public|private|protected|internal|static|override|abstract|virtual|sealed)\b)",
    re.MULTILINE,
)


def get_pattern(content: str) -> re.Pattern[str] | None:
    return _BOUNDARY
