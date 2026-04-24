# LightRAG plugin for Claude Code

A Claude Code plugin that exposes a locally-running LightRAG code-index
via MCP tools and slash-skill commands.

The plugin contains **no RAG logic** — it's a stdio MCP proxy that speaks
to a `lightrag-server` running on your machine. Evolve the server; the
plugin keeps working.

---

## Why this exists

Standard LightRAG indexes prose through LLM entity extraction — slow,
expensive, and lossy for source code. This fork adds a **code-graph
layer**: tree-sitter walks every code file deterministically, emits typed
nodes (`code_function`, `code_class`, `code_module`) and edges (`calls`,
`imports`, `inherits`, `contains`), and embeds them directly — no LLM
needed during indexing.

The result is a symbol graph you can traverse exactly:

- "Who calls `validateToken`?" → millisecond graph walk, zero LLM spend
- "What implements `IRepository`?" → same
- "Find code related to JWT" → vector search over embedded symbol bodies

Prose files (docs, configs, changelogs) still go through the LLM
extraction path unchanged.

---

## Prerequisites

1. **`lightrag-server` running locally.** Install once per user:

   ```bash
   pip install "lightrag-hku[api,codegraph]"
   lightrag-server &
   ```

   Or from this repo in development mode:

   ```bash
   uv sync --extra api --extra codegraph
   lightrag-server &
   ```

2. **Server `.env`** — minimum settings for code indexing:

   ```env
   CODE_GRAPH_ENABLED=true
   CLEANUP_ORPHANS_ON_SCAN=true    # remove symbols for deleted files
   RESOLVE_CROSS_FILE_ON_SCAN=true # rewrite short refs to full FQNs
   INPUT_DIR=/path/to/your/repo
   ```

   See `env.example` for the full reference.

3. **`uv` on PATH.** The plugin's `.mcp.json` uses `uv run` to pull
   `mcp` and `httpx` on first launch — no manual dep management needed.

4. **Claude Code 1.x** with plugin support.

---

## Install

```
/plugin install lightrag
```

Choose **project scope** to commit the plugin to the repo's
`.claude/settings.json`, or **user scope** to enable it everywhere.

---

## Configure

The MCP proxy reads two environment variables. Defaults apply when unset.

| Variable | Default | Purpose |
|---|---|---|
| `LIGHTRAG_URL` | `http://127.0.0.1:9621` | Base URL of the `lightrag-server` |
| `LIGHTRAG_API_KEY` | _(empty)_ | Bearer token if the server enforces auth |

### Pointing at a remote server

```bash
export LIGHTRAG_URL="https://rag.internal.example.com:9621"
export LIGHTRAG_API_KEY="sk-abc123..."
```

```powershell
$env:LIGHTRAG_URL    = "https://rag.internal.example.com:9621"
$env:LIGHTRAG_API_KEY = "sk-abc123..."
```

Restart Claude Code after changing these so the MCP subprocess
re-inherits the new values.

### Per-project override

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

**Semantic search**

| Tool | Endpoint | Use when |
|---|---|---|
| `search(query, mode, top_k, only_context)` | `POST /query` | fuzzy question, architecture, where-is |
| `scan()` | `POST /documents/scan` | refresh the index |
| `status()` | `GET /documents/status_counts` | check indexing progress |

**Structural graph walks** _(exact, no LLM, <100ms)_

| Tool | Use when |
|---|---|
| `find_callers(fqn)` | "who calls `mod.fn`?" |
| `find_implementers(fqn)` | "what implements `IFoo`?" |
| `find_importers(fqn)` | "who imports `requests`?" |
| `get_symbol(fqn)` | full node detail + all incident edges |

FQN format: `py:pkg.mod.fn`, `ts:src.api.fn`, `java:com.corp.Cls`,
`cs:Corp.Ns.Cls`, `js:src.util.fn`.

### Slash-skills

| Skill | What it does |
|---|---|
| `/lightrag:query <question>` | semantic + graph search |
| `/lightrag:scan` | trigger a re-index of `INPUT_DIR` |
| `/lightrag:status` | show document counts and pipeline progress |
| `/lightrag:graph` | structural graph walk (callers / implementers / importers / symbol) |
| `/lightrag:install-git-hooks` | drop git hooks so the index auto-updates on pull / commit / checkout |

### Hook

A `PreToolUse` hint fires before every `Glob` or `Grep` call, nudging
Claude to try `search` first for cross-file questions. Remove
`plugin/hooks/hooks.json` to disable.

---

## Typical workflows

### First-time setup

```
/lightrag:scan              ← index the repo (runs in background)
/lightrag:status            ← watch progress
/lightrag:install-git-hooks ← auto-update on git pull/commit/checkout
```

### Answering a structural question

```
User:  "Who calls authenticate_user, and what does it return?"

Claude: [find_callers py:auth.service.authenticate_user]
        → 4 callers in 3 files
        [Read the top caller + the function body]
        [Answers with file:line citations]
```

### Fuzzy / cross-cutting question

```
User:  "How does the payment retry logic work?"

Claude: [search query="payment retry logic" mode="hybrid"]
        → chunks + entity refs across 6 files
        [Read 2 key files to verify]
        [Answers with architecture summary]
```

### Unknown FQN → structural follow-up

```
Claude: [search query="token validator class" top_k=5]
        ← returns FQN: py:auth.validators.TokenValidator
        [find_implementers py:auth.validators.TokenValidator]
        ← returns 2 concrete classes
```

---

## Server `.env` reference (codegraph options)

| Variable | Default | Effect |
|---|---|---|
| `CODE_GRAPH_ENABLED` | `false` | Enable tree-sitter indexing for code files |
| `CLEANUP_ORPHANS_ON_SCAN` | `false` | Delete symbols for files no longer on disk. Enable only when scan covers all of `INPUT_DIR`. |
| `RESOLVE_CROSS_FILE_ON_SCAN` | `false` | Rewrite short edge targets (`py:fn`) to full FQNs after each scan. Makes structural queries navigable. O(N nodes) — disable on very large repos if scans are slow. |

---

## Supported languages

| Language | Extensions |
|---|---|
| Python | `.py` |
| TypeScript | `.ts`, `.tsx` |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` |
| Java | `.java` |
| C# | `.cs` |

Prose files (`.md`, `.txt`, `.pdf`, etc.) use the standard LLM extraction
path regardless of `CODE_GRAPH_ENABLED`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Connection refused" | `lightrag-server` isn't running. Start it. |
| "No such file: uv" | Install `uv` — `pip install uv` or see https://github.com/astral-sh/uv |
| `search` returns nothing | Run `/lightrag:status` — docs may still be pending. |
| `find_callers` returns nothing | Check `RESOLVE_CROSS_FILE_ON_SCAN=true` and re-scan; short FQNs can't be traversed until resolved. |
| Structural tools slow after schema change | Re-scan; stale edges from renamed symbols linger until orphan cleanup runs. |
| Ollama embed 400 error | Symbol body exceeds context limit — server truncates automatically since fix in `73b6d0f9`. Update your install. |

---

## Versioning

`plugin.json` version tracks the server's REST API. A breaking API change
on the server is paired with a plugin version bump.
