"""Tests for codegraph ingestion into graph + vector storage.

Uses an in-memory fake LightRAG to avoid spinning up real storage backends
or an LLM — the code path under test bypasses the LLM entirely, so a fake
is sufficient and fast.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

from lightrag.codegraph.ingest import (
    _MANIFEST_FILENAME,
    ingest_code_file,
    is_code_file,
    purge_file,
)
from lightrag.codegraph._base import NODE_CLASS, NODE_FUNCTION
from lightrag.utils import compute_mdhash_id


class _FakeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, str]] = {}
        self.edges: dict[tuple[str, str], dict[str, str]] = {}

    async def upsert_nodes_batch(self, pairs):
        for node_id, data in pairs:
            self.nodes[node_id] = data

    async def upsert_edges_batch(self, triples):
        for src, dst, data in triples:
            self.edges[(src, dst)] = data

    async def delete_node(self, node_id):
        self.nodes.pop(node_id, None)
        self.edges = {
            k: v for k, v in self.edges.items() if k[0] != node_id and k[1] != node_id
        }


class _FakeVDB:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def upsert(self, payload):
        self.rows.update(payload)

    async def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)


class _FakeRAG:
    def __init__(self, working_dir: str) -> None:
        self.working_dir = working_dir
        self.chunk_entity_relation_graph = _FakeGraph()
        self.entities_vdb = _FakeVDB()


@pytest.fixture
def rag(tmp_path):
    return _FakeRAG(str(tmp_path))


FIXTURE = Path(__file__).parent / "fixtures" / "sample.py"


def test_is_code_file():
    assert is_code_file("foo/bar.py") is True
    assert is_code_file("README.md") is False
    assert is_code_file("data.json") is False


async def test_ingest_writes_graph_and_vectors(rag):
    source = FIXTURE.read_text()
    counts = await ingest_code_file(rag, "tests/codegraph/fixtures/sample.py", source)

    assert counts["nodes"] > 0
    assert counts["edges"] > 0
    assert counts["purged_nodes"] == 0
    assert counts["embedded"] > 0

    # Every class and function got embedded.
    assert "py:tests.codegraph.fixtures.sample.Dog" in {
        r["entity_name"] for r in rag.entities_vdb.rows.values()
    }

    # Graph has the module + class + method nodes.
    assert "py:tests.codegraph.fixtures.sample" in rag.chunk_entity_relation_graph.nodes
    assert "py:tests.codegraph.fixtures.sample.Dog" in rag.chunk_entity_relation_graph.nodes
    assert (
        "py:tests.codegraph.fixtures.sample.Dog.speak"
        in rag.chunk_entity_relation_graph.nodes
    )

    # Vdb rows use mdhash keys.
    expected_key = compute_mdhash_id(
        "py:tests.codegraph.fixtures.sample.Dog.speak", prefix="ent-"
    )
    assert expected_key in rag.entities_vdb.rows
    assert rag.entities_vdb.rows[expected_key]["content"].startswith(
        "tests.codegraph.fixtures.sample.Dog.speak"
    )


async def test_non_code_file_is_noop(rag):
    counts = await ingest_code_file(rag, "README.md", "# hello")
    assert counts == {"nodes": 0, "edges": 0, "purged_nodes": 0, "embedded": 0}
    assert rag.chunk_entity_relation_graph.nodes == {}
    assert rag.entities_vdb.rows == {}


async def test_reingest_purges_stale_symbols(rag):
    path = "tests/codegraph/fixtures/sample.py"

    # First pass: ingest the full fixture.
    await ingest_code_file(rag, path, FIXTURE.read_text())
    first_node_count = len(rag.chunk_entity_relation_graph.nodes)
    first_vdb_count = len(rag.entities_vdb.rows)
    assert first_node_count > 0

    # Second pass: simulate deleting _bark_style from the file. Re-ingest
    # with reduced source; stale method node must be gone.
    trimmed = FIXTURE.read_text().replace(
        "    def _bark_style(self) -> str:\n        return \"woof\"\n", ""
    )
    # Also drop the call site to it so the fixture parses cleanly.
    trimmed = trimmed.replace(
        "        return self._bark_style()\n",
        "        return \"woof\"\n",
    )
    counts = await ingest_code_file(rag, path, trimmed)

    assert counts["purged_nodes"] == first_node_count
    assert (
        "py:tests.codegraph.fixtures.sample.Dog._bark_style"
        not in rag.chunk_entity_relation_graph.nodes
    )
    # Dog.speak still present with its updated body.
    assert "py:tests.codegraph.fixtures.sample.Dog.speak" in rag.chunk_entity_relation_graph.nodes
    # Vdb count shouldn't have grown across re-ingest (stale rows gone).
    assert len(rag.entities_vdb.rows) < first_vdb_count


async def test_manifest_persisted(rag):
    path = "tests/codegraph/fixtures/sample.py"
    await ingest_code_file(rag, path, FIXTURE.read_text())

    import json
    manifest = json.loads(
        (Path(rag.working_dir) / _MANIFEST_FILENAME).read_text()
    )
    assert path in manifest
    assert "py:tests.codegraph.fixtures.sample" in manifest[path]


async def test_purge_file_drops_everything(rag):
    path = "tests/codegraph/fixtures/sample.py"
    await ingest_code_file(rag, path, FIXTURE.read_text())
    assert rag.chunk_entity_relation_graph.nodes

    purged = await purge_file(rag, path)
    assert purged > 0
    assert rag.chunk_entity_relation_graph.nodes == {}
    assert rag.entities_vdb.rows == {}

    import json
    manifest = json.loads(
        (Path(rag.working_dir) / _MANIFEST_FILENAME).read_text()
    )
    assert path not in manifest
