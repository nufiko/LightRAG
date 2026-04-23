"""Tests for the JavaScript symbol extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")

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

FIXTURE = Path(__file__).parent / "fixtures" / "sample.js"
REL = "tests/codegraph/fixtures/sample.js"


@pytest.fixture(scope="module")
def extracted():
    ex = get_extractor(REL)
    assert ex is not None, "JS extractor should be registered for .js"
    return ex.extract(FIXTURE.read_text(), REL)


def _quals_by_type(nodes, entity_type):
    return {n.qualified_name for n in nodes if n.entity_type == entity_type}


def _ids_by_type(nodes, entity_type):
    return {n.node_id for n in nodes if n.entity_type == entity_type}


def test_all_js_extensions_registered():
    for ext in ("foo.js", "foo.jsx", "foo.mjs", "foo.cjs"):
        assert get_extractor(ext) is not None, ext


def test_js_and_ts_do_not_collide_on_prefix():
    # A .js Dog and a .ts Dog must produce different node ids.
    from lightrag.codegraph import _javascript, _typescript
    assert _javascript._LANG_PREFIX == "js"
    assert _typescript._LANG_PREFIX == "ts"


def test_file_and_module_nodes(extracted):
    nodes, _ = extracted
    assert _ids_by_type(nodes, NODE_FILE) == {f"file:{REL}"}
    assert _quals_by_type(nodes, NODE_MODULE) == {"tests.codegraph.fixtures.sample"}


def test_classes_and_methods(extracted):
    nodes, _ = extracted
    class_quals = _quals_by_type(nodes, NODE_CLASS)
    assert "tests.codegraph.fixtures.sample.Animal" in class_quals
    assert "tests.codegraph.fixtures.sample.Dog" in class_quals

    fn_quals = _quals_by_type(nodes, NODE_FUNCTION)
    assert "tests.codegraph.fixtures.sample.Dog.speak" in fn_quals
    assert "tests.codegraph.fixtures.sample.Dog._bark" in fn_quals
    assert "tests.codegraph.fixtures.sample.Dog.constructor" in fn_quals


def test_function_declarations_and_arrow_bindings(extracted):
    nodes, _ = extracted
    fn_quals = _quals_by_type(nodes, NODE_FUNCTION)
    assert "tests.codegraph.fixtures.sample.run" in fn_quals
    assert "tests.codegraph.fixtures.sample.run2" in fn_quals


def test_extends_becomes_inherits(extracted):
    _, edges = extracted
    inherits = {(e.source_id, e.target_id) for e in edges if e.relation == EDGE_INHERITS}
    assert ("js:tests.codegraph.fixtures.sample.Dog", "js:Animal") in inherits


def test_require_calls_become_imports(extracted):
    _, edges = extracted
    imports = [e for e in edges if e.relation == EDGE_IMPORTS]
    targets = {e.target_id for e in imports}
    # require('./util') and require('./helper') as imports
    assert "js:./util" in targets
    assert "js:./helper" in targets
    # ESM import too
    assert "js:./esmlib" in targets
    # require-kind marker preserved for the require-shaped ones
    require_edges = [e for e in imports if e.extra.get("kind") == "require"]
    require_targets = {e.target_id for e in require_edges}
    assert "js:./util" in require_targets
    assert "js:./helper" in require_targets


def test_require_does_not_produce_call_edge(extracted):
    """require('./x') should become an import edge, NOT a call edge."""
    _, edges = extracted
    require_call_edges = [
        e for e in edges
        if e.relation == EDGE_CALLS and e.target_id == "js:require"
    ]
    assert require_call_edges == []


def test_new_and_member_calls_from_function(extracted):
    _, edges = extracted
    calls = [e for e in edges if e.relation == EDGE_CALLS]
    run_id = "js:tests.codegraph.fixtures.sample.run"
    run_calls = [e for e in calls if e.source_id == run_id]
    targets = {e.target_id for e in run_calls}
    assert "js:Dog" in targets
    assert "js:d.speak" in targets
    assert "js:defaultFn" in targets
    dog_edges = [e for e in run_calls if e.target_id == "js:Dog"]
    assert any(e.extra.get("kind") == "constructor" for e in dog_edges)


def test_contains_edges_class_to_methods(extracted):
    _, edges = extracted
    contains = [e for e in edges if e.relation == EDGE_CONTAINS]
    dog_id = "js:tests.codegraph.fixtures.sample.Dog"
    dog_targets = {e.target_id for e in contains if e.source_id == dog_id}
    for m in ["speak", "_bark", "constructor"]:
        assert f"{dog_id}.{m}" in dog_targets
