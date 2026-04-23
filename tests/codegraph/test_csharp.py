"""Tests for the C# symbol extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_c_sharp")

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

FIXTURE = Path(__file__).parent / "fixtures" / "Sample.cs"
REL = "tests/codegraph/fixtures/Sample.cs"
FILE_SCOPED = Path(__file__).parent / "fixtures" / "SampleFileScoped.cs"
FILE_SCOPED_REL = "tests/codegraph/fixtures/SampleFileScoped.cs"


@pytest.fixture(scope="module")
def extracted():
    ex = get_extractor(REL)
    assert ex is not None, "C# extractor should be registered for .cs"
    return ex.extract(FIXTURE.read_text(), REL)


@pytest.fixture(scope="module")
def extracted_filescoped():
    ex = get_extractor(FILE_SCOPED_REL)
    return ex.extract(FILE_SCOPED.read_text(), FILE_SCOPED_REL)


def _quals_by_type(nodes, entity_type):
    return {n.qualified_name for n in nodes if n.entity_type == entity_type}


def _ids_by_type(nodes, entity_type):
    return {n.node_id for n in nodes if n.entity_type == entity_type}


def test_file_and_module_nodes(extracted):
    nodes, _ = extracted
    assert _ids_by_type(nodes, NODE_FILE) == {f"file:{REL}"}
    mod_quals = _quals_by_type(nodes, NODE_MODULE)
    assert mod_quals == {"tests.codegraph.fixtures.Sample"}


def test_namespace_pushed_into_fqn(extracted):
    """Classes inside ``namespace Coupons.Auth { ... }`` get the
    namespace in their FQN, after the file-based module prefix."""
    nodes, _ = extracted
    class_quals = _quals_by_type(nodes, NODE_CLASS)
    assert "tests.codegraph.fixtures.Sample.Coupons.Auth.Animal" in class_quals
    assert "tests.codegraph.fixtures.Sample.Coupons.Auth.Dog" in class_quals
    assert "tests.codegraph.fixtures.Sample.Coupons.Auth.IPettable" in class_quals
    assert "tests.codegraph.fixtures.Sample.Coupons.Auth.Kind" in class_quals
    assert "tests.codegraph.fixtures.Sample.Coupons.Auth.Runner" in class_quals


def test_methods_and_constructor_as_functions(extracted):
    nodes, _ = extracted
    fn_quals = _quals_by_type(nodes, NODE_FUNCTION)
    base = "tests.codegraph.fixtures.Sample.Coupons.Auth.Dog"
    assert f"{base}.Dog" in fn_quals           # constructor (same name as class)
    assert f"{base}.Speak" in fn_quals
    assert f"{base}.BarkStyle" in fn_quals
    assert f"{base}.Pet" in fn_quals
    assert "tests.codegraph.fixtures.Sample.Coupons.Auth.Runner.Run" in fn_quals


def test_base_list_produces_inherits_edges(extracted):
    _, edges = extracted
    inherits = [e for e in edges if e.relation == EDGE_INHERITS]
    pairs = {(e.source_id, e.target_id) for e in inherits}
    dog_id = "cs:tests.codegraph.fixtures.Sample.Coupons.Auth.Dog"
    # class Dog : Animal, IPettable  → both emit inherits (C# syntax doesn't
    # distinguish base class from interface here)
    assert (dog_id, "cs:Animal") in pairs
    assert (dog_id, "cs:IPettable") in pairs


def test_using_directives_produce_imports(extracted):
    _, edges = extracted
    imports = [e for e in edges if e.relation == EDGE_IMPORTS]
    targets = {e.target_id for e in imports}
    assert "cs:System" in targets
    assert "cs:System.Threading.Tasks" in targets


def test_invocation_and_new_inside_method(extracted):
    _, edges = extracted
    calls = [e for e in edges if e.relation == EDGE_CALLS]
    run_id = "cs:tests.codegraph.fixtures.Sample.Coupons.Auth.Runner.Run"
    run_calls = [e for e in calls if e.source_id == run_id]
    targets = {e.target_id for e in run_calls}
    # Run() body: new Dog("rex"); d.Speak(); System.Console.WriteLine("hi");
    assert "cs:Dog" in targets
    assert "cs:d.Speak" in targets
    assert "cs:System.Console.WriteLine" in targets
    # new Dog(...) carries kind=constructor
    dog_edges = [e for e in run_calls if e.target_id == "cs:Dog"]
    assert any(e.extra.get("kind") == "constructor" for e in dog_edges)

    # Dog.Speak body: Helper.Log(_name); BarkStyle()
    speak_id = "cs:tests.codegraph.fixtures.Sample.Coupons.Auth.Dog.Speak"
    speak_calls = [e for e in calls if e.source_id == speak_id]
    speak_targets = {e.target_id for e in speak_calls}
    assert "cs:Helper.Log" in speak_targets
    assert "cs:BarkStyle" in speak_targets


def test_contains_edges_class_to_methods(extracted):
    _, edges = extracted
    contains = [e for e in edges if e.relation == EDGE_CONTAINS]
    dog_id = "cs:tests.codegraph.fixtures.Sample.Coupons.Auth.Dog"
    dog_targets = {e.target_id for e in contains if e.source_id == dog_id}
    for m in ["Dog", "Speak", "BarkStyle", "Pet"]:
        assert f"{dog_id}.{m}" in dog_targets


def test_file_scoped_namespace(extracted_filescoped):
    """``namespace Foo.Bar;`` (C# 10+) — types declared at file level
    below the namespace statement get the namespace in their FQN."""
    nodes, edges = extracted_filescoped
    class_quals = _quals_by_type(nodes, NODE_CLASS)
    assert (
        "tests.codegraph.fixtures.SampleFileScoped.Coupons.Billing.Invoice"
        in class_quals
    )

    fn_quals = _quals_by_type(nodes, NODE_FUNCTION)
    assert (
        "tests.codegraph.fixtures.SampleFileScoped.Coupons.Billing.Invoice.Render"
        in fn_quals
    )
