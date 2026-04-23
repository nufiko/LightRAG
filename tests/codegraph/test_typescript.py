"""Tests for the TypeScript symbol extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")

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

FIXTURE = Path(__file__).parent / "fixtures" / "sample.ts"
REL = "tests/codegraph/fixtures/sample.ts"


@pytest.fixture(scope="module")
def extracted():
    ex = get_extractor(REL)
    assert ex is not None, "TypeScript extractor should be registered for .ts"
    return ex.extract(FIXTURE.read_text(), REL)


def _ids_by_type(nodes, entity_type):
    return {n.node_id for n in nodes if n.entity_type == entity_type}


def _quals_by_type(nodes, entity_type):
    return {n.qualified_name for n in nodes if n.entity_type == entity_type}


def test_tsx_extension_also_registered():
    assert get_extractor("component.tsx") is not None


def test_file_and_module_nodes(extracted):
    nodes, _ = extracted
    assert _ids_by_type(nodes, NODE_FILE) == {f"file:{REL}"}
    mod_quals = _quals_by_type(nodes, NODE_MODULE)
    assert mod_quals == {"tests.codegraph.fixtures.sample"}


def test_classes_and_interfaces_and_enums(extracted):
    nodes, _ = extracted
    class_quals = _quals_by_type(nodes, NODE_CLASS)
    assert "tests.codegraph.fixtures.sample.Animal" in class_quals
    assert "tests.codegraph.fixtures.sample.Dog" in class_quals
    # interface and enum map to code_class for symbol purposes
    assert "tests.codegraph.fixtures.sample.Pettable" in class_quals
    assert "tests.codegraph.fixtures.sample.Namable" in class_quals
    assert "tests.codegraph.fixtures.sample.Kind" in class_quals


def test_methods_get_class_qualified_fqn(extracted):
    nodes, _ = extracted
    fn_quals = _quals_by_type(nodes, NODE_FUNCTION)
    assert "tests.codegraph.fixtures.sample.Dog.speak" in fn_quals
    assert "tests.codegraph.fixtures.sample.Dog.barkStyle" in fn_quals
    assert "tests.codegraph.fixtures.sample.Dog.pet" in fn_quals


def test_const_and_var_function_expressions(extracted):
    nodes, _ = extracted
    fn_quals = _quals_by_type(nodes, NODE_FUNCTION)
    # ``const run = () => ...`` → function symbol
    assert "tests.codegraph.fixtures.sample.run" in fn_quals
    # ``export const run2 = function () {...}`` → function symbol
    assert "tests.codegraph.fixtures.sample.run2" in fn_quals


def test_extends_and_implements_both_become_inherits(extracted):
    _, edges = extracted
    inherits = [e for e in edges if e.relation == EDGE_INHERITS]
    pairs = {(e.source_id, e.target_id) for e in inherits}
    # class Dog extends Animal implements Namable
    dog_id = "ts:tests.codegraph.fixtures.sample.Dog"
    assert (dog_id, "ts:Animal") in pairs
    assert (dog_id, "ts:Namable") in pairs
    # interface Namable extends Pettable
    assert (
        "ts:tests.codegraph.fixtures.sample.Namable",
        "ts:Pettable",
    ) in pairs


def test_import_edges_point_at_module_specifier(extracted):
    _, edges = extracted
    imports = [e for e in edges if e.relation == EDGE_IMPORTS]
    targets = {e.target_id for e in imports}
    # both ``import { helper } from './utils'`` and the type-only import
    assert "ts:./utils" in targets
    assert "ts:./config" in targets


def test_new_expression_becomes_call_edge(extracted):
    _, edges = extracted
    calls = [e for e in edges if e.relation == EDGE_CALLS]
    run_calls = [e for e in calls if e.source_id == "ts:tests.codegraph.fixtures.sample.run"]
    targets = {e.target_id for e in run_calls}
    # run() body: new Dog('rex'); d.speak(); helper()
    assert "ts:Dog" in targets
    assert "ts:d.speak" in targets
    assert "ts:helper" in targets
    # the new Dog(...) call should carry kind=constructor
    dog_edges = [e for e in run_calls if e.target_id == "ts:Dog"]
    assert any(e.extra.get("kind") == "constructor" for e in dog_edges)


def test_calls_from_method_bodies(extracted):
    _, edges = extracted
    calls = [e for e in edges if e.relation == EDGE_CALLS]
    # Dog.speak() calls this.barkStyle()
    speak_calls = [
        e for e in calls
        if e.source_id == "ts:tests.codegraph.fixtures.sample.Dog.speak"
    ]
    assert any(e.target_id == "ts:this.barkStyle" for e in speak_calls)
    # Dog.barkStyle() calls helper()
    bark_calls = [
        e for e in calls
        if e.source_id == "ts:tests.codegraph.fixtures.sample.Dog.barkStyle"
    ]
    assert any(e.target_id == "ts:helper" for e in bark_calls)


def test_contains_edges_module_to_symbols(extracted):
    _, edges = extracted
    contains = [e for e in edges if e.relation == EDGE_CONTAINS]
    module_id = "ts:tests.codegraph.fixtures.sample"
    top_level_targets = {e.target_id for e in contains if e.source_id == module_id}
    # module contains Animal, Dog, run, run2, Pettable, Namable, Kind
    for name in ["Animal", "Dog", "run", "run2", "Pettable", "Namable", "Kind"]:
        assert f"ts:tests.codegraph.fixtures.sample.{name}" in top_level_targets

    # Dog contains its methods
    dog_id = "ts:tests.codegraph.fixtures.sample.Dog"
    dog_targets = {e.target_id for e in contains if e.source_id == dog_id}
    for m in ["speak", "barkStyle", "pet"]:
        assert f"{dog_id}.{m}" in dog_targets
