"""Language-to-extractor registry, separated so ingest.py can depend on it
without creating a circular import via ``lightrag.codegraph.__init__``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lightrag.codegraph import _csharp, _javascript, _python, _typescript
from lightrag.codegraph._base import SymbolExtractor

_REGISTRY: dict[str, SymbolExtractor] = {}
for _mod in (_csharp, _javascript, _python, _typescript):
    for _ext in _mod.EXTENSIONS:
        _REGISTRY[_ext] = _mod


def get_extractor(file_path: str) -> SymbolExtractor | None:
    """Return the registered extractor for *file_path*, or None."""
    ext = Path(file_path).suffix.lower()
    return _REGISTRY.get(ext)


__all__ = ["get_extractor"]
