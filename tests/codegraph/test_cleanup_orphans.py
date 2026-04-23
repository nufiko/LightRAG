"""Tests for apipeline_cleanup_orphans — detecting and deleting indexed
documents whose file_path is no longer on disk."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lightrag.codegraph.ingest import _MANIFEST_FILENAME


class _FakeDocStatus:
    def __init__(self, docs: dict[str, SimpleNamespace]) -> None:
        self.docs = docs

    async def get_docs_by_statuses(self, statuses):
        # Ignore status filter — fake returns everything. Production code
        # passes all statuses, so behavior is equivalent.
        return dict(self.docs)


class _FakeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, str]] = {}

    async def upsert_nodes_batch(self, pairs):
        for node_id, data in pairs:
            self.nodes[node_id] = data

    async def upsert_edges_batch(self, triples):
        pass

    async def delete_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)


class _FakeVDB:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def upsert(self, payload):
        self.rows.update(payload)

    async def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)


class _FakeRAG:
    """Minimal LightRAG stand-in. Only what apipeline_cleanup_orphans touches."""

    def __init__(self, working_dir: str, docs: dict[str, SimpleNamespace]) -> None:
        self.working_dir = working_dir
        self.doc_status = _FakeDocStatus(docs)
        self.chunk_entity_relation_graph = _FakeGraph()
        self.entities_vdb = _FakeVDB()
        self.deleted_doc_ids: list[str] = []

    async def adelete_by_doc_id(self, doc_id: str, delete_llm_cache: bool = False):
        self.deleted_doc_ids.append(doc_id)


def _make_doc(file_path: str) -> SimpleNamespace:
    return SimpleNamespace(file_path=file_path, status="processed")


async def _call_cleanup(rag: _FakeRAG, current: set[str]) -> dict[str, int]:
    """Invoke the production method bound to our fake rag."""
    from lightrag.lightrag import LightRAG

    # Call the method directly on our fake. The method only reads the
    # attributes we've populated on _FakeRAG, so __get__ binding works.
    return await LightRAG.apipeline_cleanup_orphans(rag, current)


async def test_empty_current_set_refuses_to_wipe(tmp_path):
    """Safety guard: caller passing an empty set must not cause a mass delete."""
    rag = _FakeRAG(
        str(tmp_path),
        docs={"d1": _make_doc("a.py"), "d2": _make_doc("b.py")},
    )
    counts = await _call_cleanup(rag, current=set())
    assert counts == {"orphans": 0, "deleted": 0, "failed": 0, "codegraph_purged": 0}
    assert rag.deleted_doc_ids == []


async def test_only_orphans_are_deleted(tmp_path):
    rag = _FakeRAG(
        str(tmp_path),
        docs={
            "d1": _make_doc("src/keep.py"),
            "d2": _make_doc("src/removed.py"),
            "d3": _make_doc("docs/also_keep.md"),
            "d4": _make_doc("src/also_removed.py"),
        },
    )
    current = {"src/keep.py", "docs/also_keep.md"}
    counts = await _call_cleanup(rag, current)

    assert counts["orphans"] == 2
    assert counts["deleted"] == 2
    assert counts["failed"] == 0
    assert set(rag.deleted_doc_ids) == {"d2", "d4"}


async def test_no_orphans(tmp_path):
    rag = _FakeRAG(
        str(tmp_path),
        docs={"d1": _make_doc("a.py"), "d2": _make_doc("b.py")},
    )
    counts = await _call_cleanup(rag, current={"a.py", "b.py", "extra.py"})
    assert counts == {"orphans": 0, "deleted": 0, "failed": 0, "codegraph_purged": 0}
    assert rag.deleted_doc_ids == []


async def test_codegraph_purge_runs_for_orphans(tmp_path):
    """When a codegraph manifest exists, orphans' codegraph symbols are
    also purged. The counter reflects actual node ids dropped from the graph."""
    # Seed the manifest + graph: the orphan has 3 codegraph nodes recorded.
    (tmp_path / _MANIFEST_FILENAME).write_text(
        '{"src/gone.py": ["py:gone.a", "py:gone.b", "py:gone.c"]}',
        encoding="utf-8",
    )
    rag = _FakeRAG(str(tmp_path), docs={"d1": _make_doc("src/gone.py")})
    # Pre-populate the graph so delete_node actually has something to remove.
    rag.chunk_entity_relation_graph.nodes = {
        "py:gone.a": {}, "py:gone.b": {}, "py:gone.c": {},
    }
    counts = await _call_cleanup(rag, current={"src/other.py"})

    assert counts["orphans"] == 1
    assert counts["deleted"] == 1
    assert counts["codegraph_purged"] == 3
    # Codegraph symbols for the orphan actually gone from the graph.
    assert rag.chunk_entity_relation_graph.nodes == {}


async def test_delete_failure_counted_and_non_fatal(tmp_path):
    """If adelete_by_doc_id raises for one doc, the loop continues with others."""

    class _FlakyRAG(_FakeRAG):
        async def adelete_by_doc_id(self, doc_id, delete_llm_cache: bool = False):
            if doc_id == "d2":
                raise RuntimeError("simulated backend hiccup")
            self.deleted_doc_ids.append(doc_id)

    rag = _FlakyRAG(
        str(tmp_path),
        docs={
            "d1": _make_doc("gone1.py"),
            "d2": _make_doc("gone2.py"),
            "d3": _make_doc("gone3.py"),
        },
    )
    counts = await _call_cleanup(rag, current={"stays.py"})

    assert counts["orphans"] == 3
    assert counts["deleted"] == 2
    assert counts["failed"] == 1
    assert set(rag.deleted_doc_ids) == {"d1", "d3"}
