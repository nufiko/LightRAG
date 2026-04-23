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
from lightrag.codegraph._registry import get_extractor
from lightrag.codegraph.ingest import ingest_code_file, is_code_file, purge_file


__all__ = [
    "CodeEdge",
    "CodeNode",
    "SymbolExtractor",
    "edge_to_storage",
    "get_extractor",
    "ingest_code_file",
    "is_code_file",
    "node_to_storage",
    "purge_file",
]
