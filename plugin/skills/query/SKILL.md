---
name: query
description: Semantic + symbol-graph search across the indexed codebase via LightRAG. Use when the question is about what code does, where something is, who calls what, or how things connect — anything that grep/glob can't answer cheaply. Also good for fuzzy queries when exact symbol names are unknown.
---

# Query LightRAG

Call the **`mcp__lightrag__search`** tool to ask the code index a question.

## When to invoke

Invoke instead of Glob/Grep when the user's question is:

- **Where-is** — "where is `authenticate_user` defined?"
- **Who-calls** — "who calls `validate_token`?", "where is `Repository.save` used?"
- **What-does** — "what does the `DependencyRegistrar` class do?"
- **Architecture** — "how does auth flow across the services?"
- **Fuzzy** — "find code related to JWT handling" (no exact symbol known)
- **Cross-cutting** — questions that span more than 2–3 files

## Parameters

- **query** (string, required) — the question. Be specific; don't stuff multiple questions in one call.
- **mode** (string, default `hybrid`):
  - `local` — good for targeted entity lookups
  - `global` — good for broad narrative questions
  - `hybrid` — balanced, the sensible default
  - `naive` — direct vector search (use when you suspect graph noise)
  - `mix` — best when a reranker is configured server-side
- **top_k** (int, default 20) — how many entities/relations to pull
- **only_context** (bool, default false) — if true, skip the LLM answer and return raw retrieved chunks + entities. Use this when you'd rather reason over the raw material yourself instead of trusting the server's synthesis.

## Output

The server returns either:
- An LLM-synthesized answer with inline citations (default), or
- A structured context block (when `only_context=true`), which you can parse and cite yourself.

## Tips

- **Start narrow.** A precise question returns a precise answer. "Who calls `login`" beats "Tell me about login."
- **Follow the citations.** The server returns `file:line` refs from the knowledge graph — open those files directly with `Read` for verification before acting.
- **Combine with `Read`.** Use the search result to find candidate files, then `Read` them to confirm behavior rather than trusting any synthesis blindly.
- **Cost** — a `mix`/`global` call on a large graph can take 10–30s against a local LLM. `hybrid` at `top_k=20` is typically 3–8s.
