---
name: graph
description: Deterministic graph walks over indexed code — find_callers, find_implementers, find_importers, get_symbol. Use when the question is structural ("who calls X", "what implements Y", "who imports Z", "give me details on symbol W"). No LLM involved; results are exact and millisecond-fast.
---

# Structural graph queries

Four MCP tools for walking the symbol graph directly — no LLM, no
vector search, just deterministic edge filtering. Results are exact
and fast.

## When to use these instead of `search`

| Question shape | Tool | Why |
|---|---|---|
| "Who calls `Foo.method`?" | `find_callers` | Direct graph walk, exact answer |
| "What implements `IRepository`?" | `find_implementers` | Walks `inherits` edges |
| "Who imports `requests`?" | `find_importers` | Walks `imports` edges |
| "Show me everything about `mod.fn`" | `get_symbol` | Node attrs + all incident edges in one call |
| "Find code about X" (no exact name) | `search` | Structural tools need FQNs — start here |

**Flow:** use `search` to discover candidate FQNs when the user's
question is fuzzy, then switch to the structural tools for precise
follow-ups.

## FQN format

Every tool takes a fully-qualified node id with a language prefix:

- Python: `py:lightrag.codegraph._python.extract`
- TypeScript: `ts:src.api.lightrag.deleteDocuments`
- JavaScript: `js:src.util.log`
- Java: `java:com.coupons.auth.Dog`
- C#: `cs:Coupons.Auth.DependenciesRegistration`

If you only know the short name (`extract`), call `search` first:

```
search(query="extract function in codegraph", top_k=5)
```

then read citations for the matching FQN, then call the structural tool.

## Output

Each list-query tool returns a markdown table with columns: source,
qualified_name, entity_type, file:line of the reference.
`get_symbol` returns a markdown summary of the node plus split tables
for outgoing / incoming edges.

## Cost

Graph walks are O(edges) server-side, typically <100ms even on large
repos. No LLM, no token spend. Prefer these over `search` whenever
the user's question is already structural.
