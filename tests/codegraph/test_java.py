"""Tests for the Java symbol extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_java")

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

FIXTURE = Path(__file__).parent / "fixtures" / "Sample.java"
REL = "tests/codegraph/fixtures/Sample.java"


@pytest.fixture(scope="module")
def extracted():
    ex = get_extractor(REL)
    assert ex is not None, "Java extractor should be registered for .java"
    return ex.extract(FIXTURE.read_text(), REL)


def _quals_by_type(nodes, entity_type):
    return {n.qualified_name for n in nodes if n.entity_type == entity_type}


def _ids_by_type(nodes, entity_type):
    return {n.node_id for n in nodes if n.entity_type == entity_type}


def test_file_and_module_nodes(extracted):
    nodes, _ = extracted
    assert _ids_by_type(nodes, NODE_FILE) == {f"file:{REL}"}
    mod_quals = _quals_by_type(nodes, NODE_MODULE)
    assert mod_quals == {"tests.codegraph.fixtures.Sample"}


def test_package_declaration_pushed_into_fqn(extracted):
    """``package com.coupons.auth;`` puts com.coupons.auth into every
    type's FQN, after the file-based module prefix."""
    nodes, _ = extracted
    class_quals = _quals_by_type(nodes, NODE_CLASS)
    for name in ["Pettable", "Namable", "Animal", "Dog", "Kind", "Point", "Runner"]:
        assert (
            f"tests.codegraph.fixtures.Sample.com.coupons.auth.{name}"
            in class_quals
        ), name


def test_nested_inner_class_has_nested_fqn(extracted):
    nodes, _ = extracted
    class_quals = _quals_by_type(nodes, NODE_CLASS)
    assert (
        "tests.codegraph.fixtures.Sample.com.coupons.auth.Dog.Collar"
        in class_quals
    )


def test_methods_constructor_and_record_method(extracted):
    nodes, _ = extracted
    fn_quals = _quals_by_type(nodes, NODE_FUNCTION)
    dog = "tests.codegraph.fixtures.Sample.com.coupons.auth.Dog"
    assert f"{dog}.Dog" in fn_quals           # constructor
    assert f"{dog}.speak" in fn_quals
    assert f"{dog}.barkStyle" in fn_quals
    assert f"{dog}.pet" in fn_quals
    assert f"{dog}.getName" in fn_quals
    # Record methods also captured
    assert (
        "tests.codegraph.fixtures.Sample.com.coupons.auth.Point.sum" in fn_quals
    )


def test_extends_and_implements_become_inherits(extracted):
    _, edges = extracted
    inherits = {(e.source_id, e.target_id) for e in edges if e.relation == EDGE_INHERITS}
    dog = "java:tests.codegraph.fixtures.Sample.com.coupons.auth.Dog"
    # class Dog extends Animal implements Namable
    assert (dog, "java:Animal") in inherits
    assert (dog, "java:Namable") in inherits
    # interface Namable extends Pettable
    namable = "java:tests.codegraph.fixtures.Sample.com.coupons.auth.Namable"
    assert (namable, "java:Pettable") in inherits


def test_imports_including_static(extracted):
    _, edges = extracted
    imports = [e for e in edges if e.relation == EDGE_IMPORTS]
    targets = {e.target_id for e in imports}
    assert "java:java.util.List" in targets
    assert "java:java.util.Map" in targets
    assert "java:com.util.Helpers.log" in targets  # static import


def test_method_invocation_with_and_without_object(extracted):
    _, edges = extracted
    calls = [e for e in edges if e.relation == EDGE_CALLS]
    run_id = "java:tests.codegraph.fixtures.Sample.com.coupons.auth.Runner.run"
    run_targets = {e.target_id for e in calls if e.source_id == run_id}
    # run() body: new Dog("rex"); d.speak(); System.out.println("hi");
    assert "java:Dog" in run_targets                    # constructor
    assert "java:d.speak" in run_targets                # object-qualified call
    assert "java:System.out.println" in run_targets     # deep chain works
    # constructor kind=constructor marker present
    dog_edges = [e for e in calls if e.source_id == run_id and e.target_id == "java:Dog"]
    assert any(e.extra.get("kind") == "constructor" for e in dog_edges)


def test_call_without_object_uses_plain_name(extracted):
    """static-imported calls (``log(name)``) carry no object prefix."""
    _, edges = extracted
    calls = [e for e in edges if e.relation == EDGE_CALLS]
    speak_id = "java:tests.codegraph.fixtures.Sample.com.coupons.auth.Dog.speak"
    speak_targets = {e.target_id for e in calls if e.source_id == speak_id}
    assert "java:log" in speak_targets
    assert "java:barkStyle" in speak_targets


def test_contains_edges_class_to_methods(extracted):
    _, edges = extracted
    contains = [e for e in edges if e.relation == EDGE_CONTAINS]
    dog_id = "java:tests.codegraph.fixtures.Sample.com.coupons.auth.Dog"
    dog_targets = {e.target_id for e in contains if e.source_id == dog_id}
    for m in ["Dog", "speak", "barkStyle", "pet", "getName", "Collar"]:
        assert f"{dog_id}.{m}" in dog_targets
