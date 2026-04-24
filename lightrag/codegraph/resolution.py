"""Cross-file symbol resolution for the code graph.

Extractors emit cross-file references with a lightweight placeholder
target id like ``py:foo`` or ``java:Animal`` and mark the edge with
``extra={"unresolved": "true"}``.  Those placeholders are not real
graph nodes on their own — NetworkX-style backends auto-create empty
stub nodes for them, which is useful for traversal but not for
answering "who calls function X".

This pass walks the entire workspace graph, builds a short-name index
from **real** symbol nodes (those with an ``entity_type`` attribute),
and rewrites every unresolved edge whose short-name has exactly one
real candidate.  Edges with zero or multiple candidates are left
alone (still marked unresolved) so subsequent runs can pick them up
as the graph grows.

The pass is idempotent — running it twice on the same graph is a
no-op after the first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightrag.utils import logger

if TYPE_CHECKING:
    from lightrag import LightRAG


# Edges for which cross-file resolution makes sense. Not ``contains`` /
# ``defined_in`` — those are locally resolved by the extractor already.
_RESOLVABLE_RELATIONS = {"calls", "imports", "inherits"}


async def resolve_cross_file_edges(rag: "LightRAG") -> dict[str, int]:
    """Rewrite unresolved edges to their fully-qualified targets where
    a unique match exists in the workspace graph.

    Returns:
        Counts dict:
            - ``real_nodes``: nodes with entity_type set (codegraph symbols)
            - ``stub_nodes``: nodes auto-created by edge insertion (no attrs)
            - ``scanned_edges``: total edges with ``unresolved:true`` marker
            - ``resolved``: edges successfully rewritten
            - ``ambiguous``: multiple candidates, left alone
            - ``unresolvable``: zero candidates, left alone
    """
    graph = rag.chunk_entity_relation_graph

    # --- Phase 1: build short-name index from real symbol nodes ------------
    all_ids = await graph.get_all_labels()
    short_index: dict[str, list[str]] = {}  # short_name -> [real fqn node_ids]
    real_nodes = 0
    stub_nodes = 0

    for node_id in all_ids:
        data = await graph.get_node(node_id)
        if not data or not data.get("entity_type"):
            stub_nodes += 1
            continue
        real_nodes += 1
        short = _short_name(
            data.get("qualified_name") or data.get("entity_name") or node_id
        )
        if short:
            short_index.setdefault(short, []).append(node_id)

    # --- Phase 2: walk every outgoing edge, rewrite unresolved ones --------
    scanned = 0
    resolved = 0
    ambiguous = 0
    unresolvable = 0

    for src_id in all_ids:
        outgoing = await graph.get_node_edges(src_id) or []
        for src, old_dst in outgoing:
            edge = await graph.get_edge(src, old_dst)
            if not edge:
                continue
            if edge.get("unresolved") != "true":
                continue
            if edge.get("relation") not in _RESOLVABLE_RELATIONS:
                # Unexpected but safe — skip rather than rewrite.
                continue

            scanned += 1
            short = _short_name_of_target(old_dst)
            if not short:
                unresolvable += 1
                continue

            candidates = short_index.get(short, [])
            # Prefer same-language if multiple candidates exist.
            if len(candidates) > 1:
                lang = _lang_prefix(old_dst)
                if lang:
                    same_lang = [c for c in candidates if _lang_prefix(c) == lang]
                    if len(same_lang) == 1:
                        candidates = same_lang

            if not candidates:
                unresolvable += 1
                continue
            if len(candidates) > 1:
                ambiguous += 1
                continue

            new_dst = candidates[0]
            new_data = {k: v for k, v in edge.items() if k != "unresolved"}
            # Keep the explicit direction property in sync with the rewritten
            # target so structural queries keep working post-resolution.
            if "dst" in new_data:
                new_data["dst"] = new_dst

            if new_dst == old_dst:
                # Already correct — just drop the marker in place.
                await graph.upsert_edge(src, old_dst, new_data)
            else:
                await graph.remove_edges([(src, old_dst)])
                await graph.upsert_edge(src, new_dst, new_data)
            resolved += 1

    counts = {
        "real_nodes": real_nodes,
        "stub_nodes": stub_nodes,
        "scanned_edges": scanned,
        "resolved": resolved,
        "ambiguous": ambiguous,
        "unresolvable": unresolvable,
    }
    logger.info(
        f"codegraph resolution: {counts['resolved']}/{counts['scanned_edges']} "
        f"edges resolved ({counts['ambiguous']} ambiguous, "
        f"{counts['unresolvable']} unresolvable) across {counts['real_nodes']} "
        f"real nodes and {counts['stub_nodes']} stub nodes"
    )
    return counts


async def prune_orphan_stubs(rag: "LightRAG") -> int:
    """Delete stub nodes (no ``entity_type``) that have no edges in or out.

    Call after ``resolve_cross_file_edges`` has rewritten everything it
    could — stubs left behind are references that never matched a real
    symbol in the workspace (e.g. Python standard-library calls).
    Removing them keeps the graph tidy; leaving them is also fine.
    """
    graph = rag.chunk_entity_relation_graph
    all_ids = await graph.get_all_labels()

    # Build set of node_ids that appear anywhere in any edge.
    touched: set[str] = set()
    for src_id in all_ids:
        outgoing = await graph.get_node_edges(src_id) or []
        for src, dst in outgoing:
            touched.add(src)
            touched.add(dst)

    pruned = 0
    for node_id in all_ids:
        if node_id in touched:
            continue
        data = await graph.get_node(node_id)
        if data and data.get("entity_type"):
            # Real isolated node (e.g., a module with no calls); keep.
            continue
        try:
            await graph.delete_node(node_id)
            pruned += 1
        except Exception as e:  # pragma: no cover - backend-specific
            logger.warning(f"prune_orphan_stubs: delete_node({node_id}) failed: {e}")
    return pruned


# --- Internals --------------------------------------------------------------


def _lang_prefix(node_id: str) -> str:
    """Extract the ``py``/``ts``/``js``/``cs``/``java`` prefix from a node id."""
    return node_id.split(":", 1)[0] if ":" in node_id else ""


def _short_name(qualified_name: str) -> str:
    """Last dotted component of a qualified name; strips lang prefix if present."""
    # Handle both ``py:pkg.mod.foo`` and bare ``pkg.mod.foo``.
    name = qualified_name.rsplit(":", 1)[-1]
    name = name.rsplit(".", 1)[-1]
    return name.strip()


def _short_name_of_target(target_id: str) -> str:
    """Extract a resolvable short name from an unresolved edge target.

    Returns empty string for targets we can't reasonably resolve via
    short-name matching (path imports like ``ts:./utils``, bare paths).
    """
    if ":" not in target_id:
        return target_id
    _, rest = target_id.split(":", 1)
    # Path-based references (TS ``./utils``, JS ``./helper``) can't be
    # resolved by short-name match — a later path-aware pass would handle
    # those. Skip them here.
    if "/" in rest:
        return ""
    return rest.rsplit(".", 1)[-1].strip()


__all__ = ["prune_orphan_stubs", "resolve_cross_file_edges"]
