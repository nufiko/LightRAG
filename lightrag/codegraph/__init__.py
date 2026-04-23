"""Code-aware graph extraction.

Uses tree-sitter to produce deterministic symbol nodes and edges from source
files, bypassing the LLM entity-extraction path used for prose. Node and edge
shapes match what ``BaseGraphStorage.upsert_node`` / ``upsert_edge`` expect,
so the same backends (NetworkX / Postgres / Neo4j) work unchanged.

Each language module in this package registers itself via EXTENSIONS and
exposes ``extract(source, file_path) -> tuple[list[CodeNode], list[CodeEdge]]``.
"""

from __future__ import annotations

from lightrag.codegraph._base import (
    CodeEdge,
    CodeNode,
    SymbolExtractor,
    edge_to_storage,
    node_to_storage,
)
from lightrag.codegraph import _python, _typescript

_REGISTRY: dict[str, SymbolExtractor] = {}
for _mod in (_python, _typescript):
    for _ext in _mod.EXTENSIONS:
        _REGISTRY[_ext] = _mod


def get_extractor(file_path: str) -> SymbolExtractor | None:
    """Return the registered extractor for *file_path*, or None."""
    from pathlib import Path

    ext = Path(file_path).suffix.lower()
    return _REGISTRY.get(ext)


__all__ = [
    "CodeEdge",
    "CodeNode",
    "SymbolExtractor",
    "edge_to_storage",
    "get_extractor",
    "node_to_storage",
]
