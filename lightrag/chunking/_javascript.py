"""JavaScript / TypeScript chunking."""

from __future__ import annotations

import re

EXTENSIONS = (".js", ".ts", ".jsx", ".tsx")

# AngularJS: split before each $scope.xxx = assignment.
_ANGULARJS_BOUNDARY = re.compile(
    r"(?=\n[ \t]*\$scope\.\w+[ \t]*=)",
    re.MULTILINE,
)

# Modern JS/TS: top-level function declarations and common function-expression assignments.
_FUNCTION_BOUNDARY = re.compile(
    r"(?=\n[ \t]*"
    r"(?:"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+"
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?function"
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\("
    r"|\w+(?:\.\w+)+\s*=\s*(?:async\s+)?function"
    r"))",
    re.MULTILINE,
)


def get_pattern(content: str) -> re.Pattern[str] | None:
    if "$scope." in content:
        return _ANGULARJS_BOUNDARY
    return _FUNCTION_BOUNDARY
