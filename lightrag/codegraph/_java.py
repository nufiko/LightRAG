"""Java symbol extractor using tree-sitter-java.

Emits:
- code_class for class / interface / enum / record / annotation-type
  declarations (plus nested inner classes)
- code_function for method_declaration and constructor_declaration
- inherits edges for ``extends Foo`` (classes) and ``implements Foo, Bar``
  (classes + interfaces share the same inherits relation)
- imports edges for import_declaration — both plain and ``static`` imports
  (target = the fully-qualified name being imported, unresolved)
- calls edges for method_invocation inside method / constructor bodies,
  composing object-qualified names (``d.speak``, ``System.out.println``)
- calls edges with kind=constructor for object_creation_expression

Package declarations push the package name onto the parent_stack in place
so all types in the file get the package in their FQN — similar to the
file-scoped namespace behavior of the C# extractor.

``tree_sitter`` and ``tree_sitter_java`` are soft deps — imported lazily
so the rest of LightRAG works without them.
"""

from __future__ import annotations

from pathlib import Path

from lightrag.codegraph._base import (
    EDGE_CALLS,
    EDGE_CONTAINS,
    EDGE_DEFINED_IN,
    EDGE_IMPORTS,
    EDGE_INHERITS,
    NODE_CLASS,
    NODE_FILE,
    NODE_FUNCTION,
    NODE_MODULE,
    CodeEdge,
    CodeNode,
)

EXTENSIONS: tuple[str, ...] = (".java",)

_LANG_PREFIX = "java"


def _module_name_from_path(file_path: str) -> str:
    parts = Path(file_path).with_suffix("").parts
    return ".".join(parts) if parts else Path(file_path).stem


def _get_parser():
    try:
        import tree_sitter_java as tsj
        from tree_sitter import Language, Parser
    except ImportError as e:
        raise ImportError(
            "Java code-graph extraction requires tree-sitter and "
            "tree-sitter-java. Install with: "
            "pip install 'lightrag-hku[codegraph]'"
        ) from e

    return Parser(Language(tsj.language()))


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _first_line(node, src: bytes) -> str:
    return _text(node, src).split("\n", 1)[0].strip()


def extract(
    source: str,
    file_path: str,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    """Parse *source* and return (nodes, edges). *file_path* must be repo-relative."""
    parser = _get_parser()
    src = source.encode("utf-8")
    tree = parser.parse(src)

    module_name = _module_name_from_path(file_path)
    module_id = f"{_LANG_PREFIX}:{module_name}"
    file_id = f"file:{file_path}"

    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []

    n_lines = max(1, source.count("\n") + 1)
    nodes.append(
        CodeNode(
            node_id=file_id, entity_type=NODE_FILE,
            name=Path(file_path).name, qualified_name=file_path,
            file_path=file_path, line_start=1, line_end=n_lines,
        )
    )
    nodes.append(
        CodeNode(
            node_id=module_id, entity_type=NODE_MODULE,
            name=module_name.rsplit(".", 1)[-1], qualified_name=module_name,
            file_path=file_path, line_start=1, line_end=n_lines,
        )
    )
    edges.append(
        CodeEdge(
            source_id=file_id, target_id=module_id,
            relation=EDGE_DEFINED_IN, file_path=file_path,
        )
    )

    _walk(
        tree.root_node, src,
        file_path=file_path, parent_stack=[module_name],
        parent_id=module_id, parent_kind="module",
        nodes=nodes, edges=edges,
    )
    return nodes, edges


def _walk(ts_node, src, *, file_path, parent_stack, parent_id, parent_kind, nodes, edges):
    for child in ts_node.children:
        _dispatch(
            child, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id, parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )


def _dispatch(node, src, *, file_path, parent_stack, parent_id, parent_kind, nodes, edges):
    kind = node.type

    if kind in (
        "class_declaration", "interface_declaration",
        "enum_declaration", "record_declaration",
        "annotation_type_declaration",
    ):
        _emit_class_like(
            node, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id,
            nodes=nodes, edges=edges,
        )
    elif kind in ("method_declaration", "constructor_declaration"):
        _emit_method(
            node, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id,
            nodes=nodes, edges=edges,
        )
    elif kind == "package_declaration":
        _emit_package(
            node, src,
            parent_stack=parent_stack,
        )
    elif kind == "import_declaration":
        _emit_import(node, src, file_path=file_path, parent_id=parent_id, edges=edges)
    elif kind == "method_invocation" and parent_kind == "function":
        _emit_invocation(node, src, file_path=file_path, parent_id=parent_id, edges=edges)
        if node.children:
            _walk(
                node, src,
                file_path=file_path, parent_stack=parent_stack,
                parent_id=parent_id, parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )
    elif kind == "object_creation_expression" and parent_kind == "function":
        _emit_new(node, src, file_path=file_path, parent_id=parent_id, edges=edges)
        if node.children:
            _walk(
                node, src,
                file_path=file_path, parent_stack=parent_stack,
                parent_id=parent_id, parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )
    else:
        if node.children:
            _walk(
                node, src,
                file_path=file_path, parent_stack=parent_stack,
                parent_id=parent_id, parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )


def _emit_package(pkg_node, src, *, parent_stack):
    """Push the package name onto parent_stack in place so subsequent
    sibling declarations inherit it in their FQN."""
    # package_declaration has a single scoped_identifier (or identifier) child.
    for c in pkg_node.children:
        if c.type in ("scoped_identifier", "identifier"):
            pkg_name = _text(c, src)
            if pkg_name:
                parent_stack.append(pkg_name)
            return


def _emit_class_like(cls_node, src, *, file_path, parent_stack, parent_id, nodes, edges):
    name_node = cls_node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, src)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id, entity_type=NODE_CLASS, name=name,
            qualified_name=qualified, file_path=file_path,
            line_start=cls_node.start_point[0] + 1,
            line_end=cls_node.end_point[0] + 1,
            signature=_first_line(cls_node, src),
        )
    )
    edges.append(
        CodeEdge(
            source_id=parent_id, target_id=node_id,
            relation=EDGE_CONTAINS, file_path=file_path,
            line=cls_node.start_point[0] + 1,
        )
    )

    # extends (classes) — wrapped in a `superclass` node
    superclass = next((c for c in cls_node.children if c.type == "superclass"), None)
    if superclass is not None:
        for s in superclass.children:
            if s.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                _emit_inherits(s, src, node_id, file_path, edges)

    # implements (classes, enums, records) — wrapped in `super_interfaces`
    super_ifaces = next(
        (c for c in cls_node.children if c.type == "super_interfaces"), None
    )
    if super_ifaces is not None:
        _emit_type_list_inherits(super_ifaces, src, node_id, file_path, edges)

    # interface extends — `extends_interfaces` node with a type_list
    extends_ifaces = next(
        (c for c in cls_node.children if c.type == "extends_interfaces"), None
    )
    if extends_ifaces is not None:
        _emit_type_list_inherits(extends_ifaces, src, node_id, file_path, edges)

    # Walk the body (class_body / interface_body / enum_body / annotation_type_body)
    body = None
    for c in cls_node.children:
        if c.type in (
            "class_body", "interface_body", "enum_body", "annotation_type_body",
        ):
            body = c
            break
    if body is not None:
        _walk(
            body, src,
            file_path=file_path, parent_stack=parent_stack + [name],
            parent_id=node_id, parent_kind="class",
            nodes=nodes, edges=edges,
        )


def _emit_type_list_inherits(container, src, source_node_id, file_path, edges):
    """Extract inherits edges from `implements`/`extends_interfaces` containers
    whose child is a `type_list` of `type_identifier`s."""
    for c in container.children:
        if c.type == "type_list":
            for t in c.children:
                if t.type in (
                    "type_identifier", "scoped_type_identifier", "generic_type",
                ):
                    _emit_inherits(t, src, source_node_id, file_path, edges)


def _emit_inherits(type_node, src, source_node_id, file_path, edges):
    # strip generic arguments: `List<Foo>` → `List`
    target = _text(type_node, src).split("<", 1)[0].strip()
    if not target:
        return
    edges.append(
        CodeEdge(
            source_id=source_node_id,
            target_id=f"{_LANG_PREFIX}:{target}",
            relation=EDGE_INHERITS, file_path=file_path,
            line=type_node.start_point[0] + 1,
            extra={"unresolved": "true"},
        )
    )


def _emit_method(m_node, src, *, file_path, parent_stack, parent_id, nodes, edges):
    name_node = m_node.child_by_field_name("name")
    if name_node is None:
        # constructor_declaration's name is an identifier child.
        name_node = next(
            (c for c in m_node.children if c.type == "identifier"), None,
        )
    if name_node is None:
        return
    name = _text(name_node, src)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id, entity_type=NODE_FUNCTION, name=name,
            qualified_name=qualified, file_path=file_path,
            line_start=m_node.start_point[0] + 1,
            line_end=m_node.end_point[0] + 1,
            signature=_first_line(m_node, src),
        )
    )
    edges.append(
        CodeEdge(
            source_id=parent_id, target_id=node_id,
            relation=EDGE_CONTAINS, file_path=file_path,
            line=m_node.start_point[0] + 1,
        )
    )

    # method_declaration: body is `block` (optional for abstract/interface methods)
    # constructor_declaration: body is `constructor_body`
    body = m_node.child_by_field_name("body")
    if body is None:
        body = next(
            (c for c in m_node.children if c.type in ("block", "constructor_body")),
            None,
        )
    if body is not None:
        _walk(
            body, src,
            file_path=file_path, parent_stack=parent_stack + [name],
            parent_id=node_id, parent_kind="function",
            nodes=nodes, edges=edges,
        )


def _emit_import(imp_node, src, *, file_path, parent_id, edges):
    # Find the fully-qualified name child (skip `import` keyword, optional
    # `static` keyword, and terminator `;`).
    target = ""
    for c in imp_node.children:
        if c.type in ("scoped_identifier", "identifier"):
            target = _text(c, src)
            break
    if not target:
        return
    edges.append(
        CodeEdge(
            source_id=parent_id,
            target_id=f"{_LANG_PREFIX}:{target}",
            relation=EDGE_IMPORTS, file_path=file_path,
            line=imp_node.start_point[0] + 1,
            extra={"unresolved": "true"},
        )
    )


def _emit_invocation(inv_node, src, *, file_path, parent_id, edges):
    name_node = inv_node.child_by_field_name("name")
    obj_node = inv_node.child_by_field_name("object")
    if name_node is None:
        return
    name = _text(name_node, src)
    if obj_node is not None:
        target = f"{_text(obj_node, src)}.{name}"
    else:
        target = name
    edges.append(
        CodeEdge(
            source_id=parent_id,
            target_id=f"{_LANG_PREFIX}:{target}",
            relation=EDGE_CALLS, file_path=file_path,
            line=inv_node.start_point[0] + 1,
            extra={"unresolved": "true"},
        )
    )


def _emit_new(new_node, src, *, file_path, parent_id, edges):
    # object_creation_expression children: [new, type, argument_list]
    t_node = None
    for c in new_node.children:
        if c.type in (
            "type_identifier", "scoped_type_identifier", "generic_type",
        ):
            t_node = c
            break
    if t_node is None:
        return
    target = _text(t_node, src).split("<", 1)[0].strip()
    if not target:
        return
    edges.append(
        CodeEdge(
            source_id=parent_id,
            target_id=f"{_LANG_PREFIX}:{target}",
            relation=EDGE_CALLS, file_path=file_path,
            line=new_node.start_point[0] + 1,
            extra={"unresolved": "true", "kind": "constructor"},
        )
    )
