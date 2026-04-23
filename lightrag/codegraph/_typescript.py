"""TypeScript / TSX symbol extractor using tree-sitter.

Uses the tree-sitter-typescript grammar's ``language_tsx()`` variant for all
inputs — it's a proper superset of plain TS, so .ts files parse cleanly too.

Emits ``code_class`` nodes for ``class``, ``interface``, and ``enum``
declarations; ``code_function`` nodes for ``function`` declarations, class
methods, and const/let/var arrow or function expressions.  Edges cover
``extends`` / ``implements`` (both as ``inherits``), ``import`` statements
(target = module specifier from the import source, left unresolved for a
later pass), plain ``call_expression`` sites, and ``new_expression``
constructor calls (``calls`` edges).

The ``tree_sitter`` and ``tree_sitter_typescript`` packages are soft deps
— imported lazily so the rest of LightRAG works without them.
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

EXTENSIONS: tuple[str, ...] = (".ts", ".tsx")

_LANG_PREFIX = "ts"


def _module_name_from_path(file_path: str) -> str:
    """Repo-relative path → dotted module name. ``index`` trims the last segment."""
    parts = Path(file_path).with_suffix("").parts
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(parts) if parts else Path(file_path).stem


def _get_parser():
    try:
        import tree_sitter_typescript as tsts
        from tree_sitter import Language, Parser
    except ImportError as e:
        raise ImportError(
            "TypeScript code-graph extraction requires tree-sitter and "
            "tree-sitter-typescript. Install with: "
            "pip install 'lightrag-hku[codegraph]'"
        ) from e

    # TSX grammar parses plain .ts cleanly and also handles JSX in .tsx.
    language = Language(tsts.language_tsx())
    return Parser(language)


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
            node_id=file_id,
            entity_type=NODE_FILE,
            name=Path(file_path).name,
            qualified_name=file_path,
            file_path=file_path,
            line_start=1,
            line_end=n_lines,
        )
    )
    nodes.append(
        CodeNode(
            node_id=module_id,
            entity_type=NODE_MODULE,
            name=module_name.rsplit(".", 1)[-1],
            qualified_name=module_name,
            file_path=file_path,
            line_start=1,
            line_end=n_lines,
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

    _walk(
        tree.root_node, src,
        file_path=file_path, module_name=module_name,
        parent_stack=[module_name], parent_id=module_id,
        parent_kind="module",
        nodes=nodes, edges=edges,
    )
    return nodes, edges


def _walk(
    ts_node, src,
    *, file_path, module_name, parent_stack, parent_id, parent_kind,
    nodes, edges,
) -> None:
    for child in ts_node.children:
        _dispatch(
            child, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack, parent_id=parent_id,
            parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )


def _dispatch(
    node, src,
    *, file_path, module_name, parent_stack, parent_id, parent_kind,
    nodes, edges,
) -> None:
    """Route a single AST node through the per-kind handlers."""
    kind = node.type

    if kind in ("class_declaration", "abstract_class_declaration"):
        _emit_class(
            node, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack, parent_id=parent_id,
            nodes=nodes, edges=edges,
        )
    elif kind == "interface_declaration":
        _emit_interface(
            node, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack, parent_id=parent_id,
            nodes=nodes, edges=edges,
        )
    elif kind == "enum_declaration":
        _emit_enum(
            node, src,
            file_path=file_path, parent_stack=parent_stack,
            parent_id=parent_id, nodes=nodes, edges=edges,
        )
    elif kind == "function_declaration":
        _emit_function(
            node, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack, parent_id=parent_id,
            parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )
    elif kind in ("lexical_declaration", "variable_declaration"):
        _emit_var_functions(
            node, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack, parent_id=parent_id,
            parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )
    elif kind == "import_statement":
        _emit_import(
            node, src,
            file_path=file_path, parent_id=parent_id, edges=edges,
        )
    elif kind == "export_statement":
        # ``export class Foo`` / ``export const run = () => {}`` — treat as
        # if the inner declaration were at the same level.
        _walk(
            node, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack, parent_id=parent_id,
            parent_kind=parent_kind,
            nodes=nodes, edges=edges,
        )
    elif kind == "call_expression" and parent_kind == "function":
        _emit_call(
            node, src,
            file_path=file_path, parent_id=parent_id, edges=edges,
        )
        # Descend so chained / nested calls get captured (args, callees).
        if node.children:
            _walk(
                node, src,
                file_path=file_path, module_name=module_name,
                parent_stack=parent_stack, parent_id=parent_id,
                parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )
    elif kind == "new_expression" and parent_kind == "function":
        _emit_new(
            node, src,
            file_path=file_path, parent_id=parent_id, edges=edges,
        )
        if node.children:
            _walk(
                node, src,
                file_path=file_path, module_name=module_name,
                parent_stack=parent_stack, parent_id=parent_id,
                parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )
    else:
        if node.children:
            _walk(
                node, src,
                file_path=file_path, module_name=module_name,
                parent_stack=parent_stack, parent_id=parent_id,
                parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )


def _emit_class(cls_node, src, *, file_path, module_name, parent_stack, parent_id, nodes, edges):
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

    # extends / implements live under class_heritage
    heritage = next((c for c in cls_node.children if c.type == "class_heritage"), None)
    if heritage is not None:
        for h in heritage.children:
            if h.type == "extends_clause":
                for e in h.children:
                    if e.type in ("identifier", "type_identifier", "member_expression"):
                        _emit_inherits(e, src, node_id, file_path, edges)
            elif h.type == "implements_clause":
                for i in h.children:
                    if i.type in ("identifier", "type_identifier", "type_reference"):
                        _emit_inherits(i, src, node_id, file_path, edges)

    body = cls_node.child_by_field_name("body")
    if body is not None:
        for member in body.children:
            if member.type == "method_definition":
                _emit_method(
                    member, src,
                    file_path=file_path, module_name=module_name,
                    parent_stack=parent_stack + [name], parent_id=node_id,
                    nodes=nodes, edges=edges,
                )


def _emit_interface(iface_node, src, *, file_path, module_name, parent_stack, parent_id, nodes, edges):
    name_node = iface_node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, src)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id, entity_type=NODE_CLASS, name=name,
            qualified_name=qualified, file_path=file_path,
            line_start=iface_node.start_point[0] + 1,
            line_end=iface_node.end_point[0] + 1,
            signature=_first_line(iface_node, src),
        )
    )
    edges.append(
        CodeEdge(
            source_id=parent_id, target_id=node_id,
            relation=EDGE_CONTAINS, file_path=file_path,
            line=iface_node.start_point[0] + 1,
        )
    )

    # interface FooBar extends Base, Other
    for c in iface_node.children:
        if c.type == "extends_type_clause":
            for e in c.children:
                if e.type in ("identifier", "type_identifier", "type_reference"):
                    _emit_inherits(e, src, node_id, file_path, edges)


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


def _emit_function(fn_node, src, *, file_path, module_name, parent_stack, parent_id, parent_kind, nodes, edges):
    name_node = fn_node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, src)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id, entity_type=NODE_FUNCTION, name=name,
            qualified_name=qualified, file_path=file_path,
            line_start=fn_node.start_point[0] + 1,
            line_end=fn_node.end_point[0] + 1,
            signature=_first_line(fn_node, src),
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
            body, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack + [name], parent_id=node_id,
            parent_kind="function",
            nodes=nodes, edges=edges,
        )


def _emit_method(method_node, src, *, file_path, module_name, parent_stack, parent_id, nodes, edges):
    name_node = method_node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, src)
    qualified = ".".join(parent_stack + [name])
    node_id = f"{_LANG_PREFIX}:{qualified}"

    nodes.append(
        CodeNode(
            node_id=node_id, entity_type=NODE_FUNCTION, name=name,
            qualified_name=qualified, file_path=file_path,
            line_start=method_node.start_point[0] + 1,
            line_end=method_node.end_point[0] + 1,
            signature=_first_line(method_node, src),
        )
    )
    edges.append(
        CodeEdge(
            source_id=parent_id, target_id=node_id,
            relation=EDGE_CONTAINS, file_path=file_path,
            line=method_node.start_point[0] + 1,
        )
    )

    body = method_node.child_by_field_name("body")
    if body is not None:
        _walk(
            body, src,
            file_path=file_path, module_name=module_name,
            parent_stack=parent_stack + [name], parent_id=node_id,
            parent_kind="function",
            nodes=nodes, edges=edges,
        )


def _emit_var_functions(var_decl_node, src, *, file_path, module_name, parent_stack, parent_id, parent_kind, nodes, edges):
    """Handle ``const run = () => {...}`` and ``let fn = function() {...}``.

    We only emit a function node when the bound value is an arrow_function or
    function_expression — otherwise it's a regular data binding, not a symbol.
    """
    for declarator in var_decl_node.children:
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        value_node = declarator.child_by_field_name("value")
        if name_node is None or value_node is None:
            continue
        if value_node.type not in ("arrow_function", "function_expression"):
            # Not a function binding — but the value itself may be a call
            # target we care about (``const d = new Dog(...)``) or contain
            # nested ones.  Dispatch the value node through the same
            # switch so call/new handlers fire when parent_kind=="function".
            _dispatch(
                value_node, src,
                file_path=file_path, module_name=module_name,
                parent_stack=parent_stack, parent_id=parent_id,
                parent_kind=parent_kind,
                nodes=nodes, edges=edges,
            )
            continue
        if name_node.type != "identifier":
            continue

        name = _text(name_node, src)
        qualified = ".".join(parent_stack + [name])
        node_id = f"{_LANG_PREFIX}:{qualified}"

        nodes.append(
            CodeNode(
                node_id=node_id, entity_type=NODE_FUNCTION, name=name,
                qualified_name=qualified, file_path=file_path,
                line_start=value_node.start_point[0] + 1,
                line_end=value_node.end_point[0] + 1,
                signature=_first_line(declarator, src),
            )
        )
        edges.append(
            CodeEdge(
                source_id=parent_id, target_id=node_id,
                relation=EDGE_CONTAINS, file_path=file_path,
                line=value_node.start_point[0] + 1,
            )
        )

        body = value_node.child_by_field_name("body")
        if body is not None:
            _walk(
                body, src,
                file_path=file_path, module_name=module_name,
                parent_stack=parent_stack + [name], parent_id=node_id,
                parent_kind="function",
                nodes=nodes, edges=edges,
            )


def _emit_inherits(type_node, src, source_node_id, file_path, edges):
    target = _text(type_node, src).split("<", 1)[0].strip()  # strip generic args
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


def _emit_import(imp_node, src, *, file_path, parent_id, edges):
    source_node = imp_node.child_by_field_name("source")
    if source_node is None:
        return
    # ``source`` is a string node; its first string_fragment child is the text.
    target = ""
    for c in source_node.children:
        if c.type == "string_fragment":
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


def _emit_call(call_node, src, *, file_path, parent_id, edges):
    fn_field = call_node.child_by_field_name("function")
    if fn_field is None:
        return
    if fn_field.type not in ("identifier", "member_expression"):
        return
    target_name = _text(fn_field, src)
    edges.append(
        CodeEdge(
            source_id=parent_id,
            target_id=f"{_LANG_PREFIX}:{target_name}",
            relation=EDGE_CALLS, file_path=file_path,
            line=call_node.start_point[0] + 1,
            extra={"unresolved": "true"},
        )
    )


def _emit_new(new_node, src, *, file_path, parent_id, edges):
    ctor = new_node.child_by_field_name("constructor")
    if ctor is None:
        return
    if ctor.type not in ("identifier", "member_expression"):
        return
    target_name = _text(ctor, src)
    edges.append(
        CodeEdge(
            source_id=parent_id,
            target_id=f"{_LANG_PREFIX}:{target_name}",
            relation=EDGE_CALLS, file_path=file_path,
            line=new_node.start_point[0] + 1,
            extra={"unresolved": "true", "kind": "constructor"},
        )
    )
