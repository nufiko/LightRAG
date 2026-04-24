"""Tests for structural query helpers over the code graph."""

from __future__ import annotations

import pytest

from lightrag.codegraph.queries import (
    find_callers,
    find_implementers,
    find_importers,
    get_symbol,
)


class _FakeGraph:
    """Mimics BaseGraphStorage, including the undirected-backend quirk:
    ``get_all_edges`` can yield the same edge twice with swapped
    source/target (Neo4j reality). Direction-sensitive queries must
    rely on edge['src'] / edge['dst'] properties, not on those columns."""

    def __init__(self, undirected_duplication: bool = False) -> None:
        self.nodes: dict[str, dict[str, str]] = {}
        self.edges: list[dict[str, str]] = []
        self.undirected_duplication = undirected_duplication

    def add_node(self, node_id: str, **attrs) -> None:
        self.nodes[node_id] = dict(attrs)

    def add_edge(
        self,
        src: str,
        dst: str,
        relation: str,
        *,
        file_path: str = "",
        line: int = 0,
        unresolved: bool = False,
        kind: str = "",
    ) -> None:
        data = {
            "relation": relation,
            "src": src,
            "dst": dst,
            "source": src,
            "target": dst,
            "file_path": file_path,
            "line": str(line),
        }
        if unresolved:
            data["unresolved"] = "true"
        if kind:
            data["kind"] = kind
        self.edges.append(data)

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def get_all_edges(self) -> list[dict[str, str]]:
        if not self.undirected_duplication:
            return list(self.edges)
        # Return each edge twice, swapping source/target — Neo4j behavior.
        result = []
        for e in self.edges:
            result.append(dict(e))
            swap = dict(e)
            swap["source"], swap["target"] = swap["target"], swap["source"]
            result.append(swap)
        return result


# ---------------------------------------------------------------------------


async def test_find_callers_returns_src_side_nodes():
    g = _FakeGraph()
    g.add_node("py:caller1", entity_type="code_function", qualified_name="mod.caller1")
    g.add_node("py:caller2", entity_type="code_function", qualified_name="mod.caller2")
    g.add_node("py:target", entity_type="code_function", qualified_name="mod.target")
    g.add_node("py:other", entity_type="code_function", qualified_name="mod.other")
    g.add_edge("py:caller1", "py:target", "calls", file_path="a.py", line=10)
    g.add_edge("py:caller2", "py:target", "calls", file_path="b.py", line=20)
    g.add_edge("py:caller1", "py:other", "calls", file_path="a.py", line=30)  # noise

    hits = await find_callers(g, "py:target")

    assert len(hits) == 2
    sources = {h["source"] for h in hits}
    assert sources == {"py:caller1", "py:caller2"}
    # File + line of the call site come from the edge
    files = {h["file_path"] for h in hits}
    assert files == {"a.py", "b.py"}
    # Node attributes come from get_node
    assert all(h["qualified_name"].startswith("mod.caller") for h in hits)
    assert all(h["entity_type"] == "code_function" for h in hits)


async def test_find_callers_survives_undirected_backend():
    """Neo4j returns each edge twice (swapped source/target). find_callers
    must dedupe on the explicit src/dst properties."""
    g = _FakeGraph(undirected_duplication=True)
    g.add_node("py:caller", entity_type="code_function", qualified_name="mod.caller")
    g.add_node("py:target", entity_type="code_function", qualified_name="mod.target")
    g.add_edge("py:caller", "py:target", "calls", file_path="a.py", line=10)

    hits = await find_callers(g, "py:target")

    # The backend gave us the edge twice; we should see ONE caller, not two.
    assert len(hits) == 1
    assert hits[0]["source"] == "py:caller"
    # And looking up "py:caller" as the target (reversed edge from the
    # duplication) must return nothing — because src/dst say otherwise.
    assert await find_callers(g, "py:caller") == []


async def test_find_implementers_filters_by_relation():
    g = _FakeGraph()
    g.add_node("java:Dog", entity_type="code_class", qualified_name="com.x.Dog")
    g.add_node("java:Animal", entity_type="code_class", qualified_name="com.x.Animal")
    g.add_node("java:Runner", entity_type="code_class", qualified_name="com.x.Runner")
    g.add_edge("java:Dog", "java:Animal", "inherits", file_path="Dog.java", line=5)
    g.add_edge("java:Runner", "java:Dog", "calls", file_path="Run.java", line=9)  # wrong rel

    hits = await find_implementers(g, "java:Animal")

    assert len(hits) == 1
    assert hits[0]["source"] == "java:Dog"


async def test_find_importers_on_module_target():
    g = _FakeGraph()
    g.add_node("py:app", entity_type="code_module", qualified_name="src.app")
    g.add_node("py:utils", entity_type="code_module", qualified_name="src.utils")
    g.add_edge("py:app", "py:utils", "imports", file_path="src/app.py", line=1)

    hits = await find_importers(g, "py:utils")

    assert len(hits) == 1
    assert hits[0]["source"] == "py:app"
    assert hits[0]["line"] == 1


async def test_find_callers_on_unknown_target_returns_empty():
    g = _FakeGraph()
    g.add_node("py:caller", entity_type="code_function", qualified_name="mod.caller")
    g.add_edge("py:caller", "py:other", "calls")

    assert await find_callers(g, "py:nonexistent") == []


async def test_get_symbol_returns_node_plus_split_edges():
    g = _FakeGraph()
    g.add_node(
        "py:mod.middle",
        entity_type="code_function", qualified_name="mod.middle",
        file_path="mod.py", line_start="10", line_end="20",
    )
    g.add_node("py:mod.up", entity_type="code_function", qualified_name="mod.up")
    g.add_node("py:mod.down1", entity_type="code_function", qualified_name="mod.down1")
    g.add_node("py:mod.down2", entity_type="code_function", qualified_name="mod.down2")
    # Incoming: up calls middle
    g.add_edge("py:mod.up", "py:mod.middle", "calls", file_path="mod.py", line=5)
    # Outgoing: middle calls down1, down2
    g.add_edge("py:mod.middle", "py:mod.down1", "calls", file_path="mod.py", line=12)
    g.add_edge(
        "py:mod.middle", "py:mod.down2", "calls",
        file_path="mod.py", line=15, kind="constructor",
    )

    detail = await get_symbol(g, "py:mod.middle")

    assert detail is not None
    assert detail["fqn"] == "py:mod.middle"
    assert detail["is_stub"] is False
    assert detail["node"]["entity_type"] == "code_function"
    assert detail["node"]["line_start"] == "10"

    assert len(detail["incoming"]) == 1
    assert detail["incoming"][0]["relation"] == "calls"
    assert detail["incoming"][0]["other"] == "py:mod.up"

    assert len(detail["outgoing"]) == 2
    outs = {e["other"]: e for e in detail["outgoing"]}
    assert set(outs) == {"py:mod.down1", "py:mod.down2"}
    assert outs["py:mod.down2"]["kind"] == "constructor"


async def test_get_symbol_missing_returns_none():
    g = _FakeGraph()
    assert await get_symbol(g, "py:nothing") is None


async def test_get_symbol_flags_stub_node():
    g = _FakeGraph()
    g.add_node("py:stub")  # no entity_type
    detail = await get_symbol(g, "py:stub")
    assert detail is not None
    assert detail["is_stub"] is True


async def test_unresolved_marker_survives_into_get_symbol():
    g = _FakeGraph()
    g.add_node("py:caller", entity_type="code_function", qualified_name="mod.caller")
    g.add_node("py:target", entity_type="code_function", qualified_name="mod.target")
    g.add_edge(
        "py:caller", "py:target", "calls",
        file_path="mod.py", line=3, unresolved=True,
    )
    detail = await get_symbol(g, "py:caller")
    assert detail["outgoing"][0]["unresolved"] is True
