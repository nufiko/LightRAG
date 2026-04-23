"""Python symbol extractor using tree-sitter.

Extracts modules, classes, functions/methods, imports, inheritance, and
intra-function calls. Nested functions/classes get fully qualified names
based on their lexical parent chain.

The ``tree_sitter`` and ``tree_sitter_python`` packages are soft deps —
imported lazily so the rest of LightRAG works without them.
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

EXTENSIONS: tuple[str, ...] = (".py",)

_LANG_PREFIX = "py"


def _module_name_from_path(file_path: str) -> str:
    """Heuristic: repo-relative path → dotted module name (stem; ``__init__`` drops last)."""
    parts = Path(file_path).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else Path(file_path).stem


def _get_parser():
    """Lazy import so the package is usable without tree-sitter installed."""
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser
    except ImportError as e:
        raise ImportError(
            "Python code-graph extraction requires tree-sitter and "
            "tree-sitter-python. Install with: "
            "pip install 'lightrag-hku[codegraph]'"
        ) from e

    language = Language(tspython.language())
    return Parser(language)


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _first_line(node, source_bytes: bytes) -> str:
    text = _node_text(node, source_bytes)
    return text.split("\n", 1)[0].strip()


def extract(
    source: str,
    file_path: str,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    """Parse *source* and return (nodes, edges).

    *file_path* must be repo-relative.
    """
    parser = _get_parser()
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    module_name = _module_name_from_path(file_path)
    module_id = f"{_LANG_PREFIX}:{module_name}"
    file_id = f"file:{file_path}"

    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []

    # File node — lets us answer "what's in this file" cheaply.
    nodes.append(
        CodeNode(
            node_id=file_id,
            entity_type=NODE_FILE,
            name=Path(file_path).name,
            qualified_name=file_path,
            file_path=file_path,
            line_start=1,
            line_end=max(1, source.count("\n") + 1),
        )
    )

    # Module node.
    nodes.append(
        CodeNode(
            node_id=module_id,
            entity_type=NODE_MODULE,
            name=module_name.rsplit(".", 1)[-1],
            qualified_name=module_name,
            file_path=file_path,
            line_start=1,
            line_end=max(1, source.count("\n") + 1),
        )
    )
    edges.append(
        CodeEdge(
            source_id=file_id,
            target_id=module_id,
            relation=EDGE_DEFINED_IN,
            file_path=file_path,
        )
    )

    # Walk the tree, tracking the lexical parent stack for FQN construction.
    _walk(
        tree.root_node,
        source_bytes,
        file_path=file_path,
        module_name=module_name,
        parent_stack=[module_name],
        parent_id=module_id,
        parent_kind="module",
        nodes=nodes,
        edges=edges,
    )

    return nodes, edges


def _walk(
    ts_node,
    source_bytes: bytes,
    *,
    file_path: str,
    module_name: str,
    parent_stack: list[str],
    parent_id: str,
    parent_kind: str,
    nodes: list[CodeNode],
    edges: list[CodeEdge],
) -> None:
    """Recursively walk a tree-sitter node, emitting symbols and edges."""
    for child in ts_node.children:
        kind = child.type

        if kind == "class_definition":
            _emit_class(
                child, source_bytes,
                file_path=file_path, module_name=module_name,
                parent_stack=parent_stack, parent_id=parent_id,
                nodes=nodes, edges=edges,
            )
        elif kind == "function_definition":
            _emit_function(
                child, source_bytes,
                file_path=file_path, module_name=module_name,
                parent_stack=parent_stack, parent_id=parent_id,
                parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )
        elif kind in ("import_statement", "import_from_statement"):
            _emit_import(
                child, source_bytes,
                file_path=file_path, parent_id=parent_id,
                edges=edges,
            )
        elif kind == "call" and parent_kind == "function":
            _emit_call(
                child, source_bytes,
                file_path=file_path, parent_id=parent_id,
                edges=edges,
            )
        else:
            # Descend into compound statements (if/for/while/try/...) so we
            # still find nested defs and calls inside a function body.
            if child.children:
                _walk(
                    child, source_bytes,
                    file_path=file_path, module_name=module_name,
                    parent_stack=parent_stack, parent_id=parent_id,
                    parent_kind=parent_kind,
                    nodes=nodes, edges=edges,
                )


def _emit_class(
    cls_node, source_bytes,
    *, file_path, module_name, parent_stack, parent_id,
    nodes, edges,
) -> None:
    name_node = cls_node.child_by_field_name("name")
    if name_node is None:
        return
    name = _node_text(name_node, source_bytes)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id,
            entity_type=NODE_CLASS,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            line_start=cls_node.start_point[0] + 1,
            line_end=cls_node.end_point[0] + 1,
            signature=_first_line(cls_node, source_bytes),
        )
    )
    edges.append(
        CodeEdge(
            source_id=parent_id, target_id=node_id,
            relation=EDGE_CONTAINS, file_path=file_path,
            line=cls_node.start_point[0] + 1,
        )
    )

    # Inheritance: superclasses field is an argument_list of identifiers.
    superclasses = cls_node.child_by_field_name("superclasses")
    if superclasses is not None:
        for sup in superclasses.children:
            if sup.type in ("identifier", "attribute"):
                base = _node_text(sup, source_bytes)
                edges.append(
                    CodeEdge(
                        source_id=node_id,
                        target_id=f"{_LANG_PREFIX}:{base}",
                        relation=EDGE_INHERITS, file_path=file_path,
                        line=sup.start_point[0] + 1,
                        extra={"unresolved": "true"},
                    )
                )

    # Recurse into class body.
    body = cls_node.child_by_field_name("body")
    if body is not None:
        _walk(
            body, source_bytes,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack + [name], parent_id=node_id,
            parent_kind="class",
            nodes=nodes, edges=edges,
        )


def _emit_function(
    fn_node, source_bytes,
    *, file_path, module_name, parent_stack, parent_id, parent_kind,
    nodes, edges,
) -> None:
    name_node = fn_node.child_by_field_name("name")
    if name_node is None:
        return
    name = _node_text(name_node, source_bytes)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id,
            entity_type=NODE_FUNCTION,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            line_start=fn_node.start_point[0] + 1,
            line_end=fn_node.end_point[0] + 1,
            signature=_first_line(fn_node, source_bytes),
        )
    )
    edges.append(
        CodeEdge(
            source_id=parent_id, target_id=node_id,
            relation=EDGE_CONTAINS, file_path=file_path,
            line=fn_node.start_point[0] + 1,
        )
    )

    body = fn_node.child_by_field_name("body")
    if body is not None:
        _walk(
            body, source_bytes,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack + [name], parent_id=node_id,
            parent_kind="function",
            nodes=nodes, edges=edges,
        )


def _emit_import(
    imp_node, source_bytes,
    *, file_path, parent_id, edges,
) -> None:
    # Handle both ``import a.b`` and ``from a import b`` forms.
    # We emit one unresolved edge per imported name; resolution happens in a
    # later pass when the full project graph is available.
    if imp_node.type == "import_statement":
        for child in imp_node.children:
            if child.type in ("dotted_name", "aliased_import"):
                target_text = _dotted_or_aliased(child, source_bytes)
                if target_text:
                    edges.append(
                        CodeEdge(
                            source_id=parent_id,
                            target_id=f"{_LANG_PREFIX}:{target_text}",
                            relation=EDGE_IMPORTS, file_path=file_path,
                            line=imp_node.start_point[0] + 1,
                            extra={"unresolved": "true"},
                        )
                    )
    else:  # import_from_statement
        module_node = imp_node.child_by_field_name("module_name")
        module_target = _node_text(module_node, source_bytes) if module_node else ""
        # The from-module itself is an import edge.
        if module_target:
            edges.append(
                CodeEdge(
                    source_id=parent_id,
                    target_id=f"{_LANG_PREFIX}:{module_target}",
                    relation=EDGE_IMPORTS, file_path=file_path,
                    line=imp_node.start_point[0] + 1,
                    extra={"unresolved": "true"},
                )
            )


def _dotted_or_aliased(n, source_bytes: bytes) -> str:
    if n.type == "dotted_name":
        return _node_text(n, source_bytes)
    if n.type == "aliased_import":
        name = n.child_by_field_name("name")
        if name is not None:
            return _node_text(name, source_bytes)
    return ""


def _emit_call(
    call_node, source_bytes,
    *, file_path, parent_id, edges,
) -> None:
    fn_field = call_node.child_by_field_name("function")
    if fn_field is None:
        return
    # Attribute access (``self.foo()``, ``m.bar()``) and plain identifiers both
    # make sense as call targets. Anything else (lambdas, subscripts) skip.
    if fn_field.type not in ("identifier", "attribute"):
        return
    target_name = _node_text(fn_field, source_bytes)
    edges.append(
        CodeEdge(
            source_id=parent_id,
            target_id=f"{_LANG_PREFIX}:{target_name}",
            relation=EDGE_CALLS, file_path=file_path,
            line=call_node.start_point[0] + 1,
            extra={"unresolved": "true"},
        )
    )
