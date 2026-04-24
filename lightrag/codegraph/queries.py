"""Structural queries over the code graph.

Four helpers for the "who calls / who implements / who imports / tell me
about this symbol" family of questions that Claude Code hammers on while
investigating a codebase.

Every codegraph edge stored by ``edge_to_storage`` carries explicit
``src`` and ``dst`` properties — those are what we filter on, not the
backend's own source/target columns.  Backends that store edges
undirectedly (Neo4j ``-[r]-``) otherwise return each edge twice with
swapped endpoints, which would confuse direction-sensitive queries
like ``find_callers``.
"""

from __future__ import annotations

from typing import Any

from lightrag.utils import logger


async def find_callers(graph, target: str) -> list[dict[str, Any]]:
    """Return nodes with a ``calls`` edge pointing AT *target*.

    For code symbols the convention is src → caller, dst → callee. So
    callers of ``py:foo.bar`` are all ``src`` values on ``calls`` edges
    with ``dst == py:foo.bar``.
    """
    return await _find_incoming(graph, target, "calls")


async def find_implementers(graph, target: str) -> list[dict[str, Any]]:
    """Return nodes with an ``inherits`` edge pointing AT *target*.

    Covers both class-subclass (``class Dog extends Animal``) and
    interface-implementation (``class Dog implements Pettable``) —
    both emit the same ``inherits`` relation.
    """
    return await _find_incoming(graph, target, "inherits")


async def find_importers(graph, target: str) -> list[dict[str, Any]]:
    """Return nodes with an ``imports`` edge pointing AT *target*.

    Target is typically a module id (``py:requests``, ``ts:./utils``,
    ``java:java.util.List``). Caller gets back the file(s) that import it.
    """
    return await _find_incoming(graph, target, "imports")


async def get_symbol(graph, fqn: str) -> dict[str, Any] | None:
    """Return full detail for a single symbol: node attributes + incident
    edges split into outgoing/incoming.

    Returns None if no node with id *fqn* exists. Stub nodes (no
    ``entity_type``) are reported but flagged so callers can filter.
    """
    node = await graph.get_node(fqn)
    if node is None:
        return None

    outgoing: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    seen_out: set[tuple[str, str, str]] = set()
    seen_in: set[tuple[str, str, str]] = set()

    for edge in await graph.get_all_edges() or []:
        src = edge.get("src")
        dst = edge.get("dst")
        relation = edge.get("relation")
        if not src or not dst or not relation:
            continue
        if src == fqn:
            key = (src, dst, relation)
            if key in seen_out:
                continue
            seen_out.add(key)
            outgoing.append(_edge_summary(edge, other=dst))
        elif dst == fqn:
            key = (src, dst, relation)
            if key in seen_in:
                continue
            seen_in.add(key)
            incoming.append(_edge_summary(edge, other=src))

    return {
        "fqn": fqn,
        "node": dict(node),
        "is_stub": not node.get("entity_type"),
        "outgoing": outgoing,
        "incoming": incoming,
    }


# --- Internals --------------------------------------------------------------


async def _find_incoming(graph, target: str, relation: str) -> list[dict[str, Any]]:
    """Return src-side nodes of all edges with (dst == target, relation).

    Each result dict includes the source node's qualified_name and
    entity_type (looked up via get_node) plus the edge's call-site
    file_path + line.
    """
    try:
        all_edges = await graph.get_all_edges() or []
    except Exception as e:  # pragma: no cover - backend error, best-effort report
        logger.warning(f"codegraph queries: get_all_edges failed: {e}")
        return []

    seen: set[tuple[str, str]] = set()
    hits: list[dict[str, Any]] = []

    for edge in all_edges:
        if edge.get("relation") != relation:
            continue
        if edge.get("dst") != target:
            continue
        src = edge.get("src")
        if not src:
            continue
        key = (src, target)
        if key in seen:
            continue
        seen.add(key)

        src_node = await graph.get_node(src) or {}
        hits.append(
            {
                "source": src,
                "qualified_name": src_node.get("qualified_name", ""),
                "entity_type": src_node.get("entity_type", ""),
                "file_path": edge.get("file_path", ""),
                "line": _int_or_zero(edge.get("line")),
            }
        )
    return hits


def _edge_summary(edge: dict[str, Any], *, other: str) -> dict[str, Any]:
    """Compact summary for an edge when returned from get_symbol."""
    return {
        "relation": edge.get("relation", ""),
        "other": other,
        "file_path": edge.get("file_path", ""),
        "line": _int_or_zero(edge.get("line")),
        "unresolved": edge.get("unresolved") == "true",
        "kind": edge.get("kind", ""),
    }


def _int_or_zero(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "find_callers",
    "find_implementers",
    "find_importers",
    "get_symbol",
]
