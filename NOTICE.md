# NOTICE

This fork of HKUDS/LightRAG adds:

- Tree-sitter symbol extraction for Python, TypeScript/TSX, JavaScript,
  Java, and C# (`lightrag/codegraph/`). The extractor emits real
  `code_function` / `code_class` / `code_module` nodes plus `calls`,
  `imports`, `inherits`, and `contains` edges — bypassing the LLM
  entity-extraction path for code files. See `PLAN_CODEGRAPH.md` for
  the full design.
- A Claude Code plugin (`plugin/`) that exposes the resulting index
  via MCP tools.

## Prior art

The idea of **deterministic AST-based symbol extraction with
Claude Code integration via a `PreToolUse` hook and a knowledge-graph
skill** was pioneered by [graphify](https://github.com/safishamsi/graphify)
(MIT). This codebase does not vendor or fork graphify — the extractor
(`lightrag/codegraph/*`) and the Claude Code integration (`plugin/`)
are independent native implementations on top of LightRAG's existing
`BaseGraphStorage` + `BaseVectorStorage` architecture and its
multi-backend scalability (Neo4j, Postgres, Milvus, etc.), which
graphify's NetworkX-only storage doesn't target.

Graphify remains a reference for design ideas. Track its releases
for new language grammars or AST-query patterns worth porting.

## Upstream

This fork tracks [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG)
(MIT). All additive changes live under `lightrag/codegraph/`,
`plugin/`, and small surgical edits to `lightrag/lightrag.py` and
`lightrag/api/routers/document_routes.py`; upstream rebases stay
cheap as long as the additive pattern is preserved.
