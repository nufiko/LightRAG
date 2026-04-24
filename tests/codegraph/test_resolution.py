"""Tests for cross-file resolution of unresolved symbol edges."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lightrag.codegraph.resolution import (
    prune_orphan_stubs,
    resolve_cross_file_edges,
)


class _FakeGraph:
    """In-memory fake that supports the slice of BaseGraphStorage the
    resolution pass exercises — labels, nodes, edges, upsert/remove."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, str]] = {}
        self.edges: dict[tuple[str, str], dict[str, str]] = {}

    async def get_all_labels(self) -> list[str]:
        return sorted(self.nodes.keys())

    async def get_node(self, node_id: str) -> dict[str, str] | None:
        return self.nodes.get(node_id)

    async def get_node_edges(self, src_id: str) -> list[tuple[str, str]]:
        return [(s, d) for (s, d) in self.edges if s == src_id]

    async def get_edge(self, src: str, dst: str) -> dict[str, str] | None:
        return self.edges.get((src, dst))

    async def upsert_edge(self, src: str, dst: str, data: dict[str, str]) -> None:
        self.nodes.setdefault(src, {})
        self.nodes.setdefault(dst, {})
        self.edges[(src, dst)] = dict(data)

    async def remove_edges(self, pairs) -> None:
        for p in pairs:
            self.edges.pop(p, None)

    async def delete_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        self.edges = {
            k: v for k, v in self.edges.items()
            if k[0] != node_id and k[1] != node_id
        }

    # Helpers for test setup -------------------------------------------------
    def add_real(self, node_id: str, entity_type: str, qualified_name: str) -> None:
        self.nodes[node_id] = {
            "entity_id": node_id,
            "entity_type": entity_type,
            "qualified_name": qualified_name,
        }

    def add_stub(self, node_id: str) -> None:
        # Mimic NetworkX auto-created nodes — present but with no entity_type.
        self.nodes.setdefault(node_id, {})

    def add_edge(
        self, src: str, dst: str, *, relation: str, unresolved: bool = True,
    ) -> None:
        data: dict[str, str] = {"relation": relation}
        if unresolved:
            data["unresolved"] = "true"
        self.nodes.setdefault(src, {})
        self.nodes.setdefault(dst, {})
        self.edges[(src, dst)] = data


class _FakeRAG:
    def __init__(self) -> None:
        self.chunk_entity_relation_graph = _FakeGraph()


# ---- tests -----------------------------------------------------------------


async def test_unique_short_name_resolves_and_drops_marker():
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    # Real target symbol
    g.add_real(
        "py:lightrag.codegraph._python.extract",
        "code_function",
        "lightrag.codegraph._python.extract",
    )
    # Caller node + stub target
    g.add_real(
        "py:lightrag.codegraph._python.run",
        "code_function",
        "lightrag.codegraph._python.run",
    )
    g.add_stub("py:extract")
    g.add_edge(
        "py:lightrag.codegraph._python.run",
        "py:extract",
        relation="calls",
    )

    counts = await resolve_cross_file_edges(rag)

    assert counts["scanned_edges"] == 1
    assert counts["resolved"] == 1
    assert counts["ambiguous"] == 0
    assert counts["unresolvable"] == 0

    # Old edge gone
    assert ("py:lightrag.codegraph._python.run", "py:extract") not in g.edges
    # New edge points to real FQN, marker dropped
    new_edge = g.edges[
        ("py:lightrag.codegraph._python.run", "py:lightrag.codegraph._python.extract")
    ]
    assert new_edge["relation"] == "calls"
    assert "unresolved" not in new_edge


async def test_ambiguous_short_name_left_alone():
    """If two real nodes share a short name, the edge stays unresolved
    so a smarter pass (or a user) can disambiguate later."""
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("py:pkg.a.Dog", "code_class", "pkg.a.Dog")
    g.add_real("py:pkg.b.Dog", "code_class", "pkg.b.Dog")
    g.add_real("py:pkg.main.run", "code_function", "pkg.main.run")
    g.add_edge("py:pkg.main.run", "py:Dog", relation="calls")

    counts = await resolve_cross_file_edges(rag)

    assert counts["ambiguous"] == 1
    assert counts["resolved"] == 0
    # Edge untouched, marker preserved
    assert g.edges[("py:pkg.main.run", "py:Dog")]["unresolved"] == "true"


async def test_same_language_preference_breaks_ties():
    """``java:Dog`` should match the java Dog, not the python one, even
    though both share the short name."""
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("py:pkg.Dog", "code_class", "pkg.Dog")
    g.add_real("java:com.coupons.Dog", "code_class", "com.coupons.Dog")
    g.add_real("java:com.coupons.Runner.run", "code_function", "com.coupons.Runner.run")
    g.add_edge("java:com.coupons.Runner.run", "java:Dog", relation="calls")

    counts = await resolve_cross_file_edges(rag)

    assert counts["resolved"] == 1
    assert ("java:com.coupons.Runner.run", "java:com.coupons.Dog") in g.edges
    assert ("java:com.coupons.Runner.run", "java:Dog") not in g.edges


async def test_unresolvable_target_left_alone():
    """A target with no matching real node stays unresolved."""
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("py:pkg.main.run", "code_function", "pkg.main.run")
    g.add_edge("py:pkg.main.run", "py:some_stdlib_fn", relation="calls")

    counts = await resolve_cross_file_edges(rag)

    assert counts["unresolvable"] == 1
    assert counts["resolved"] == 0
    assert g.edges[("py:pkg.main.run", "py:some_stdlib_fn")]["unresolved"] == "true"


async def test_path_targets_are_unresolvable_short_name():
    """TS relative-path imports (``ts:./utils``) are skipped by the
    short-name resolver — they need a path-aware pass."""
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("ts:src.utils", "code_module", "src.utils")
    g.add_real("ts:src.app", "code_module", "src.app")
    g.add_edge("ts:src.app", "ts:./utils", relation="imports")

    counts = await resolve_cross_file_edges(rag)
    assert counts["resolved"] == 0
    assert counts["unresolvable"] == 1


async def test_non_resolvable_relations_are_skipped():
    """``contains`` / ``defined_in`` edges are locally resolved by the
    extractor; the pass should leave them strictly alone even if they
    carry an unresolved marker by mistake."""
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("py:pkg.mod", "code_module", "pkg.mod")
    g.add_real("py:pkg.mod.run", "code_function", "pkg.mod.run")
    g.add_edge("py:pkg.mod", "py:run", relation="contains")

    counts = await resolve_cross_file_edges(rag)
    assert counts["resolved"] == 0
    assert counts["scanned_edges"] == 0


async def test_idempotent_second_run_is_noop():
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("py:mod.foo", "code_function", "mod.foo")
    g.add_real("py:mod.run", "code_function", "mod.run")
    g.add_edge("py:mod.run", "py:foo", relation="calls")

    first = await resolve_cross_file_edges(rag)
    assert first["resolved"] == 1

    second = await resolve_cross_file_edges(rag)
    assert second["resolved"] == 0
    assert second["scanned_edges"] == 0


async def test_prune_orphan_stubs_drops_isolated_stubs():
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("py:mod.foo", "code_function", "mod.foo")
    g.add_stub("py:floating_stub")       # no edges → orphan
    g.add_stub("py:still_referenced")    # keeps an edge
    g.add_edge("py:mod.foo", "py:still_referenced", relation="calls")

    pruned = await prune_orphan_stubs(rag)

    assert pruned == 1
    assert "py:floating_stub" not in g.nodes
    assert "py:still_referenced" in g.nodes
    assert "py:mod.foo" in g.nodes


async def test_prune_orphan_stubs_keeps_real_isolated_nodes():
    """A real node with no edges (e.g., a module with no calls) must
    not be pruned — entity_type is set."""
    rag = _FakeRAG()
    g = rag.chunk_entity_relation_graph
    g.add_real("py:mod.lonely", "code_module", "mod.lonely")
    pruned = await prune_orphan_stubs(rag)
    assert pruned == 0
    assert "py:mod.lonely" in g.nodes
