"""C# symbol extractor using tree-sitter-c-sharp.

Emits:
- code_class for class, interface, struct, record, enum declarations
- code_function for method_declaration and constructor_declaration nodes
- inherits edges for base_list entries (C# syntax does not distinguish
  extends vs. implements; both land as inherits)
- imports edges for using_directive (targets the imported name/qualified
  name, left unresolved for a later pass)
- calls edges for invocation_expression nodes inside function bodies
- calls edges with kind=constructor for object_creation_expression

Namespace declarations (``namespace Foo.Bar { ... }`` block form and
the C# 10+ ``namespace Foo.Bar;`` file-scoped form) push onto the
parent_stack so class FQNs include their containing namespace.

``tree_sitter`` and ``tree_sitter_c_sharp`` are soft deps — imported
lazily so the rest of LightRAG works without them.
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

EXTENSIONS: tuple[str, ...] = (".cs",)

_LANG_PREFIX = "cs"


def _module_name_from_path(file_path: str) -> str:
    parts = Path(file_path).with_suffix("").parts
    return ".".join(parts) if parts else Path(file_path).stem


def _get_parser():
    try:
        import tree_sitter_c_sharp as tscs
        from tree_sitter import Language, Parser
    except ImportError as e:
        raise ImportError(
            "C# code-graph extraction requires tree-sitter and "
            "tree-sitter-c-sharp. Install with: "
            "pip install 'lightrag-hku[codegraph]'"
        ) from e

    return Parser(Language(tscs.language()))


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

    if kind in ("class_declaration", "struct_declaration",
                "interface_declaration", "record_declaration",
                "record_struct_declaration"):
        _emit_class_like(
            node, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id,
            nodes=nodes, edges=edges,
        )
    elif kind == "enum_declaration":
        _emit_enum(
            node, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id,
            nodes=nodes, edges=edges,
        )
    elif kind in ("method_declaration", "constructor_declaration",
                  "local_function_statement"):
        _emit_method(
            node, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id, parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )
    elif kind in ("namespace_declaration", "file_scoped_namespace_declaration"):
        _emit_namespace(
            node, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id, parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )
    elif kind == "using_directive":
        _emit_using(node, src, file_path=file_path, parent_id=parent_id, edges=edges)
    elif kind == "invocation_expression" and parent_kind == "function":
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


def _emit_namespace(ns_node, src, *, file_path, parent_stack, parent_id, parent_kind, nodes, edges):
    # Namespaces are organizational; they don't become symbol nodes in our
    # graph.  We just push their name(s) onto the parent_stack so classes
    # inside get the correct FQN.  For file-scoped namespaces the
    # declarations that follow appear as *siblings* in the AST, so we need
    # to special-case that traversal.
    name_node = ns_node.child_by_field_name("name")
    ns_name = _text(name_node, src) if name_node is not None else ""
    new_stack = parent_stack + ([ns_name] if ns_name else [])

    if ns_node.type == "file_scoped_namespace_declaration":
        # Continue walking siblings of the namespace declaration at
        # the current level with the new stack.  The caller's _walk
        # already passed all children to _dispatch, so here we just
        # need to handle any block-body children on this node (none
        # for file-scoped form) — and recurse into the parent by
        # updating the stack for subsequent siblings.  Since _walk
        # iterates children serially, the simplest fix is to mutate
        # parent_stack *in place*.
        parent_stack.clear()
        parent_stack.extend(new_stack)
        return

    # Block form: walk the namespace's declaration_list with the pushed stack.
    body = ns_node.child_by_field_name("body")
    if body is None:
        # Fallback to scanning children for declaration_list.
        body = next(
            (c for c in ns_node.children if c.type == "declaration_list"),
            None,
        )
    if body is not None:
        _walk(
            body, src,
            file_path=file_path, parent_stack=new_stack,
            parent_id=parent_id, parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )


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

    # Base list: `: Base, IOther, IAnother<T>`
    base_list = next((c for c in cls_node.children if c.type == "base_list"), None)
    if base_list is not None:
        for b in base_list.children:
            if b.type in ("identifier", "qualified_name", "generic_name"):
                target = _text(b, src).split("<", 1)[0].strip()
                if target:
                    edges.append(
                        CodeEdge(
                            source_id=node_id,
                            target_id=f"{_LANG_PREFIX}:{target}",
                            relation=EDGE_INHERITS, file_path=file_path,
                            line=b.start_point[0] + 1,
                            extra={"unresolved": "true"},
                        )
                    )

    # Walk body (declaration_list) with the class pushed onto parent_stack.
    body = next(
        (c for c in cls_node.children if c.type == "declaration_list"),
        None,
    )
    if body is not None:
        _walk(
            body, src,
            file_path=file_path, parent_stack=parent_stack + [name],
            parent_id=node_id, parent_kind="class",
            nodes=nodes, edges=edges,
        )


def _emit_enum(enum_node, src, *, file_path, parent_stack, parent_id, nodes, edges):
    name_node = enum_node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, src)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id, entity_type=NODE_CLASS, name=name,
            qualified_name=qualified, file_path=file_path,
            line_start=enum_node.start_point[0] + 1,
            line_end=enum_node.end_point[0] + 1,
            signature=_first_line(enum_node, src),
        )
    )
    edges.append(
        CodeEdge(
            source_id=parent_id, target_id=node_id,
            relation=EDGE_CONTAINS, file_path=file_path,
            line=enum_node.start_point[0] + 1,
        )
    )


def _emit_method(m_node, src, *, file_path, parent_stack, parent_id, parent_kind, nodes, edges):
    name_node = m_node.child_by_field_name("name")
    if name_node is None:
        # constructor_declaration has no explicit name field — its
        # identifier child echoes the class name.
        name_node = next(
            (c for c in m_node.children if c.type == "identifier"),
            None,
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

    body = m_node.child_by_field_name("body")
    if body is None:
        body = next((c for c in m_node.children if c.type == "block"), None)
    if body is not None:
        _walk(
            body, src,
            file_path=file_path, parent_stack=parent_stack + [name],
            parent_id=node_id, parent_kind="function",
            nodes=nodes, edges=edges,
        )


def _emit_using(u_node, src, *, file_path, parent_id, edges):
    # Children (after the `using` keyword): identifier, qualified_name, or
    # name_equals followed by qualified_name / identifier (``using X = Y``).
    for c in u_node.children:
        if c.type in ("identifier", "qualified_name"):
            target = _text(c, src)
            if target:
                edges.append(
                    CodeEdge(
                        source_id=parent_id,
                        target_id=f"{_LANG_PREFIX}:{target}",
                        relation=EDGE_IMPORTS, file_path=file_path,
                        line=u_node.start_point[0] + 1,
                        extra={"unresolved": "true"},
                    )
                )
            break


def _emit_invocation(inv_node, src, *, file_path, parent_id, edges):
    fn_field = inv_node.child_by_field_name("function")
    if fn_field is None:
        return
    if fn_field.type not in ("identifier", "member_access_expression", "qualified_name"):
        return
    target = _text(fn_field, src)
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
    t_field = new_node.child_by_field_name("type")
    if t_field is None:
        # Fall back: first identifier / qualified_name after the `new` keyword.
        t_field = next(
            (c for c in new_node.children
             if c.type in ("identifier", "qualified_name", "generic_name")),
            None,
        )
    if t_field is None or t_field.type not in (
        "identifier", "qualified_name", "generic_name"
    ):
        return
    target = _text(t_field, src).split("<", 1)[0].strip()
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
