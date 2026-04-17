"""Java chunking: split on method/class boundaries."""

from __future__ import annotations

import re

EXTENSIONS = (".java",)

# Blank line before optional annotations then an access/modifier keyword.
_BOUNDARY = re.compile(
    r"(?=\n[ \t]*\n"
    r"(?:[ \t]*@\w+(?:\([^)]*\))?[ \t]*\n)*"
    r"[ \t]+"
    r"(?:public|private|protected|static|final|abstract|synchronized|native|default)\b)",
    re.MULTILINE,
)


def get_pattern(content: str) -> re.Pattern[str] | None:
    return _BOUNDARY
