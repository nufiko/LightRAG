# LightRAG plugin for Claude Code

A thin Claude Code plugin that exposes a locally-running LightRAG
code-index via three MCP tools (`search`, `scan`, `status`) and three
corresponding slash-skill commands.

The plugin contains **no RAG logic itself** — it's a stdio MCP proxy
that speaks to a `lightrag-server` running on your machine. Evolve the
server, the plugin keeps working.

---

## Prerequisites

1. **`lightrag-server` running locally.** Install once per user:

   ```bash
   uv tool install "lightrag-hku[api,codegraph]" \
       --from git+https://github.com/<you>/lightrag-fork@main
   lightrag-server &   # background daemon
   ```

   Set `CODE_GRAPH_ENABLED=true` in the server's `.env` to get
   tree-sitter code-aware indexing (otherwise the plugin still works
   over prose-only LLM-extracted entities).

2. **`uv` on PATH.** The plugin's `.mcp.json` uses `uv run` to pull
   `mcp` and `httpx` on first launch, so you don't need to manage
   Python deps manually.

3. **Claude Code 1.x** with plugin support.

---

## Install

From Claude Code:

```
/plugin marketplace add <you>/lightrag-fork
/plugin install lightrag@lightrag-fork
```

Choose **project scope** to commit the plugin choice to the repo's
`.claude/settings.json`, or **user scope** to enable it everywhere.

## Configure

The MCP proxy reads two environment variables. Defaults apply when
unset — point the plugin at a different server by exporting them in
your shell (they flow through Claude Code into the proxy's subprocess).

| Var | Default | Purpose |
|---|---|---|
| `LIGHTRAG_URL` | `http://127.0.0.1:9621` | Base URL of the `lightrag-server` to query |
| `LIGHTRAG_API_KEY` | empty | Bearer token if the server enforces auth |

### Pointing at a remote / shared server

```bash
# On Linux / macOS
export LIGHTRAG_URL="https://rag.internal.groupondev.com:9621"
export LIGHTRAG_API_KEY="sk-abc123..."
```

```powershell
# On Windows PowerShell
$env:LIGHTRAG_URL   = "https://rag.internal.groupondev.com:9621"
$env:LIGHTRAG_API_KEY = "sk-abc123..."
```

Restart Claude Code after changing these so the MCP subprocess
re-inherits the new values.

### Per-project override

If you want a per-repo `LIGHTRAG_URL` (e.g. a local dev server for one
checkout, a shared team server for another), add an `env` block in
the project's `.mcp.json` — that overrides any shell env:

```json
{
  "mcpServers": {
    "lightrag": {
      "env": { "LIGHTRAG_URL": "http://localhost:9622" }
    }
  }
}
```

---

## What's exposed

### MCP tools

| Tool | Verb | Use when |
|---|---|---|
| `search(query, mode, top_k, only_context)` | `POST /query` | semantic + graph question, where-is, who-calls, architecture |
| `scan()` | `POST /documents/scan` | user wants to refresh the index |
| `status()` | `GET /documents/status_counts` + `/pipeline_status` | user wants to see indexing progress |

### Slash-skills

- `/lightrag:query <question>`
- `/lightrag:scan`
- `/lightrag:status`

### Hook

A `PreToolUse` hint fires before every `Glob` or `Grep` call, nudging
Claude to consider the `search` MCP tool for cross-file semantic
questions. Cosmetic — disable by removing `plugin/hooks/hooks.json` if
you don't want the noise.

---

## Workflow example

```
User: where is authenticate_user defined, and who calls it?

Claude:  [invokes mcp__lightrag__search with query="authenticate_user definition and callers"]
         → 3 definition candidates, 7 callers across 2 services
         [Reads the top definition to confirm]
         [Answers with file:line citations]
```

---

## Troubleshooting

- **"Connection refused"** — `lightrag-server` isn't running. Start it.
- **"No such file: uv"** — install `uv` (https://github.com/astral-sh/uv).
- **Tool returns empty text** — check `/lightrag:status`; may indicate no docs indexed yet or all docs at `pending`.
- **`search` is slow on broad questions** — use `mode: "local"` or a narrower query, or lower `top_k`.

---

## Versioning

The plugin's `version` in `plugin.json` follows the engine's
minor-version cadence — a breaking REST-API change on the server is
paired with a plugin version bump.
