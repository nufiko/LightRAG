"""Tests for the Python symbol extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole module if tree-sitter isn't installed (codegraph extra not enabled).
pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

from lightrag.codegraph import get_extractor
from lightrag.codegraph._base import (
    EDGE_CALLS,
    EDGE_CONTAINS,
    EDGE_IMPORTS,
    EDGE_INHERITS,
    NODE_CLASS,
    NODE_FILE,
    NODE_FUNCTION,
    NODE_MODULE,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.py"


@pytest.fixture(scope="module")
def extracted():
    extractor = get_extractor(str(FIXTURE))
    assert extractor is not None, "Python extractor should be registered for .py"
    source = FIXTURE.read_text()
    nodes, edges = extractor.extract(source, "tests/codegraph/fixtures/sample.py")
    return nodes, edges


def _ids_by_type(nodes, entity_type):
    return {n.node_id for n in nodes if n.entity_type == entity_type}


def _qualified_by_type(nodes, entity_type):
    return {n.qualified_name for n in nodes if n.entity_type == entity_type}


def test_emits_file_and_module_nodes(extracted):
    nodes, _ = extracted
    assert _ids_by_type(nodes, NODE_FILE) == {"file:tests/codegraph/fixtures/sample.py"}
    module_quals = _qualified_by_type(nodes, NODE_MODULE)
    assert module_quals == {"tests.codegraph.fixtures.sample"}


def test_top_level_function(extracted):
    nodes, _ = extracted
    fn_quals = _qualified_by_type(nodes, NODE_FUNCTION)
    assert "tests.codegraph.fixtures.sample.top_level_helper" in fn_quals
    assert "tests.codegraph.fixtures.sample.run" in fn_quals


def test_classes_and_methods_have_fqn(extracted):
    nodes, _ = extracted
    class_quals = _qualified_by_type(nodes, NODE_CLASS)
    assert "tests.codegraph.fixtures.sample.Animal" in class_quals
    assert "tests.codegraph.fixtures.sample.Dog" in class_quals

    fn_quals = _qualified_by_type(nodes, NODE_FUNCTION)
    # Methods get the class name in their FQN.
    assert "tests.codegraph.fixtures.sample.Dog.speak" in fn_quals
    assert "tests.codegraph.fixtures.sample.Dog._bark_style" in fn_quals
    assert "tests.codegraph.fixtures.sample.Animal.speak" in fn_quals


def test_inheritance_edge_emitted(extracted):
    _, edges = extracted
    inherits = [e for e in edges if e.relation == EDGE_INHERITS]
    assert any(
        e.source_id == "py:tests.codegraph.fixtures.sample.Dog"
        and e.target_id == "py:Animal"
        for e in inherits
    ), f"Dog -> Animal inheritance edge missing; saw: {inherits}"


def test_import_edges_emitted(extracted):
    _, edges = extracted
    imports = [e for e in edges if e.relation == EDGE_IMPORTS]
    targets = {e.target_id for e in imports}
    # ``import os`` and ``from pathlib import Path as P``
    assert "py:os" in targets
    assert "py:pathlib" in targets


def test_call_edges_from_function_bodies(extracted):
    _, edges = extracted
    calls = [e for e in edges if e.relation == EDGE_CALLS]
    # ``run`` calls Dog(), d.speak(), top_level_helper(1), P(".").resolve()
    run_calls = [e for e in calls if e.source_id == "py:tests.codegraph.fixtures.sample.run"]
    targets = {e.target_id for e in run_calls}
    assert "py:Dog" in targets
    assert "py:top_level_helper" in targets
    assert "py:d.speak" in targets

    # ``Dog.speak`` calls self._bark_style() and os.getenv(...)
    speak_calls = [e for e in calls if e.source_id == "py:tests.codegraph.fixtures.sample.Dog.speak"]
    speak_targets = {e.target_id for e in speak_calls}
    assert "py:self._bark_style" in speak_targets
    assert "py:os.getenv" in speak_targets


def test_contains_edges_module_to_symbols(extracted):
    _, edges = extracted
    contains = [e for e in edges if e.relation == EDGE_CONTAINS]
    module_id = "py:tests.codegraph.fixtures.sample"
    # Module contains top-level functions and classes.
    top_level_targets = {
        e.target_id for e in contains if e.source_id == module_id
    }
    assert "py:tests.codegraph.fixtures.sample.Animal" in top_level_targets
    assert "py:tests.codegraph.fixtures.sample.Dog" in top_level_targets
    assert "py:tests.codegraph.fixtures.sample.top_level_helper" in top_level_targets
    assert "py:tests.codegraph.fixtures.sample.run" in top_level_targets

    # Class contains its methods.
    dog_targets = {
        e.target_id for e in contains if e.source_id == "py:tests.codegraph.fixtures.sample.Dog"
    }
    assert "py:tests.codegraph.fixtures.sample.Dog.speak" in dog_targets
    assert "py:tests.codegraph.fixtures.sample.Dog._bark_style" in dog_targets


def test_storage_adapter_shapes():
    from lightrag.codegraph import edge_to_storage, node_to_storage
    from lightrag.codegraph._base import CodeEdge, CodeNode

    n = CodeNode(
        node_id="py:x.y",
        entity_type=NODE_FUNCTION,
        name="y",
        qualified_name="x.y",
        file_path="x.py",
        line_start=3,
        line_end=5,
    )
    node_id, data = node_to_storage(n)
    assert node_id == "py:x.y"
    assert data["entity_type"] == NODE_FUNCTION
    assert data["source_id"] == "x.py:3-5"
    assert data["line_start"] == "3"

    e = CodeEdge(
        source_id="py:x.y", target_id="py:x.z",
        relation=EDGE_CALLS, file_path="x.py", line=4,
    )
    src, dst, edata = edge_to_storage(e)
    assert (src, dst) == ("py:x.y", "py:x.z")
    assert edata["relation"] == EDGE_CALLS
    assert edata["line"] == "4"
