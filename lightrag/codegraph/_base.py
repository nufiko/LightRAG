"""Dataclasses and protocol for code symbol extractors.

Extractors produce ``CodeNode`` and ``CodeEdge`` instances which are then
converted to the flat dict shape expected by ``BaseGraphStorage``. Keeping
the typed form inside the codegraph package lets per-language modules stay
legible; only the adapter boundary deals with dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


# Entity type values used for code symbols. Prefixed with ``code_`` so they
# never collide with prose entity types (person / organization / concept / ...).
NODE_MODULE = "code_module"
NODE_CLASS = "code_class"
NODE_FUNCTION = "code_function"
NODE_FILE = "code_file"

# Edge relation values stored under edge_data["relation"].
EDGE_CONTAINS = "contains"
EDGE_DEFINED_IN = "defined_in"
EDGE_CALLS = "calls"
EDGE_IMPORTS = "imports"
EDGE_INHERITS = "inherits"


@dataclass(frozen=True)
class CodeNode:
    """A code symbol (module, class, function, or file)."""

    # Fully qualified, language-prefixed id, e.g. ``py:pkg.mod.Class.method``.
    # Must be stable across re-index runs for the same symbol.
    node_id: str
    entity_type: str  # one of NODE_*
    name: str         # short name (``method``), for display
    qualified_name: str  # ``pkg.mod.Class.method`` without language prefix
    file_path: str    # repo-relative
    line_start: int
    line_end: int
    description: str = ""  # filled later by optional LLM pass
    signature: str = ""    # raw signature / first-line snippet

    def source_id(self) -> str:
        """Used as chunk-like reference for provenance."""
        return f"{self.file_path}:{self.line_start}-{self.line_end}"


@dataclass(frozen=True)
class CodeEdge:
    """A directed edge between two code symbols."""

    source_id: str  # node_id of source
    target_id: str  # node_id of target
    relation: str   # one of EDGE_*
    file_path: str  # repo-relative file where the edge was observed
    line: int = 0   # line number of the reference site (calls/imports)
    # Extra metadata (e.g. import alias, call args count); optional.
    extra: dict[str, str] = field(default_factory=dict)


class SymbolExtractor(Protocol):
    """Per-language extractor contract.

    Implementations live in ``lightrag.codegraph._<lang>`` and advertise the
    extensions they handle via a module-level ``EXTENSIONS`` tuple.
    """

    EXTENSIONS: tuple[str, ...]

    @staticmethod
    def extract(
        source: str,
        file_path: str,
    ) -> tuple[list[CodeNode], list[CodeEdge]]:
        """Parse *source* and return (nodes, edges).

        *file_path* must be repo-relative so provenance fields are portable
        across machines.
        """


# --- Adapter boundary -------------------------------------------------------


def node_to_storage(node: CodeNode) -> tuple[str, dict[str, str]]:
    """Convert a CodeNode to the (node_id, node_data) pair BaseGraphStorage expects.

    Note: ``entity_id`` must be present inside node_data — the Neo4j backend
    enforces it (properties must contain 'entity_id') and the LLM extraction
    path also sets it (see operate.py: ``"entity_id": node_id``). NetworkX
    doesn't enforce it, which is why early smoke tests on the fake/NetworkX
    backends didn't catch this.
    """
    return (
        node.node_id,
        {
            "entity_id": node.node_id,
            "entity_type": node.entity_type,
            "entity_name": node.name,
            "qualified_name": node.qualified_name,
            "description": node.description,
            "signature": node.signature,
            "file_path": node.file_path,
            "line_start": str(node.line_start),
            "line_end": str(node.line_end),
            "source_id": node.source_id(),
        },
    )


def edge_to_storage(
    edge: CodeEdge,
) -> tuple[str, str, dict[str, str]]:
    """Convert a CodeEdge to the (src, dst, edge_data) triple BaseGraphStorage expects.

    The edge_data dict includes ``src`` and ``dst`` as explicit properties
    so direction survives the trip through backends that store edges
    undirectedly (Neo4j's MERGE (a)-[r:DIRECTED]-(b) drops direction;
    get_all_edges returns each edge twice with swapped endpoints).
    Structural queries like "who calls X" rely on these fields rather
    than the backend's source/target.
    """
    data: dict[str, str] = {
        "relation": edge.relation,
        "file_path": edge.file_path,
        "line": str(edge.line),
        "source_id": f"{edge.file_path}:{edge.line}",
        "src": edge.source_id,
        "dst": edge.target_id,
    }
    for k, v in edge.extra.items():
        data[k] = v
    return (edge.source_id, edge.target_id, data)
