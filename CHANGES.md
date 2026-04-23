# LightRAG — Local Setup & Changes

This document describes the local configuration and code changes applied to run LightRAG
against a local codebase using Ollama + PostgreSQL + Neo4j.

---

## How to Run

### Prerequisites

| Service | Version | Purpose |
|---------|---------|---------|
| Ollama | latest | LLM + embedding inference |
| PostgreSQL | 14+ | Vector + KV + document status storage |
| Neo4j | 5+ | Knowledge graph storage |

Ollama models required:
```bash
ollama pull qwen2.5-coder:14b   # LLM
ollama pull nomic-embed-text     # Embeddings
```

### Start the Server

```bash
cd C:\Git\LightRAG

# Activate virtual environment
.venv\Scripts\activate

# Start server (UTF-8 flag required on Windows to avoid encoding errors)
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 lightrag-server
```

Server is available at: http://localhost:9621

### Index a Codebase

1. Open the WebUI at http://localhost:9621
2. Go to **Documents** tab
3. Click **Scan** — LightRAG reads all files from `INPUT_DIR` in `.env`
4. Wait for all files to move from `pending` → `processed`

### Query

Use the **Chat** tab in the WebUI. Recommended query modes:
- `hybrid` — good general-purpose
- `mix` — best when a reranker is configured
- `local` — focused on specific entities

---

## Configuration (`.env`)

Key settings changed from defaults:

```env
# Source directory to index
INPUT_DIR=C:\Git\Minas2Test

# LLM — code-focused model, same VRAM footprint as qwen2.5:14b
LLM_MODEL=qwen2.5-coder:14b

# Reduced from 32768 to keep model fully in VRAM (16GB GPU)
# At 32768 the KV cache pushed ~4GB to CPU, dropping GPU utilization to 11%
# At 16384 the full model fits in VRAM, GPU utilization ~94%
OLLAMA_NUM_CTX=16384
OLLAMA_LLM_NUM_CTX=16384

# Token budget for query context (must satisfy: entity + relation < total)
# Reduced proportionally from defaults to match the smaller context window
MAX_ENTITY_TOKENS=4000
MAX_RELATION_TOKENS=6000
MAX_TOTAL_TOKENS=12000
```

---

## Code Changes

### 1. Pipeline Deadlock Fix (`lightrag/api/routers/document_routes.py`)

**Problem:** After enqueuing all files, the LLM extraction pipeline never started.
Log showed: `"Another process is already processing"`.

**Root cause:** `_set_status()` defaults `busy=True`. Calling it immediately before
`apipeline_process_enqueue_documents()` caused the pipeline to see `busy=True` and
return early with `request_pending=True`.

**Fix:** Pass `busy=False` to the final status update before handing off to the pipeline:

```python
await _set_status(
    f"All {total_valid} files enqueued, starting LLM extraction...",
    cur=total_valid,
    busy=False,  # Let apipeline_process_enqueue_documents own the busy flag
)
await rag.apipeline_process_enqueue_documents()
```

---

### 2. Vendor Directory Exclusions (`document_routes.py`)

Added to `EXCLUDED_DIRS` so vendor/third-party source trees are not indexed
(they add noise without contributing organizational knowledge):

```python
"vendor", "vendors", "bower_components", "third_party", "thirdparty", "externals"
```

Full updated set also includes: `node_modules`, `.git`, `.vs`, `.idea`, `.vscode`,
`bin`, `obj`, `dist`, `build`, `out`, `output`, `packages`, `.nuget`, `TestResults`,
`coverage`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `worktrees`, `__enqueued__`,
`test`, `tests`, `__tests__`, `spec`, `specs`.

---

### 3. Minified File Exclusions (`document_routes.py`)

Added to `EXCLUDED_FILE_SUFFIXES` so bundled/minified assets are skipped
(they are semantically opaque to the LLM and waste tokens):

```python
".min.js", ".min.css",
".bundle.js", ".bundle.css",
"-min.js", "-min.css",
```

---

## GPU Utilization Notes

With `qwen2.5-coder:14b` and a 16GB VRAM GPU:

| `num_ctx` | Model size | VRAM used | GPU utilization |
|-----------|-----------|-----------|----------------|
| 32768 | ~17.2 GB | ~12.9 GB (4.3 GB on CPU) | ~11% |
| 16384 | ~12.6 GB | ~12.6 GB (fully in VRAM) | ~94% |

To verify current model placement:
```bash
curl http://localhost:11434/api/ps
# Check: size_vram should equal size (no CPU offload)
```

To force-unload a model (e.g. after changing `num_ctx`):
```bash
curl -X POST http://localhost:11434/api/generate \
  -d '{"model": "qwen2.5-coder:14b", "keep_alive": 0}'
```

---

## Document Status Reference

| Status | Meaning |
|--------|---------|
| `pending` | Enqueued, waiting for LLM extraction. `chunks = -1` is normal (not yet chunked). |
| `processing` | Currently being processed by the LLM pipeline |
| `processed` | Fully indexed into the knowledge graph |
| `preprocessed` | Text indexed; awaiting multimodal (image) pass — unused in pure-text setups |
| `failed` | LLM extraction failed (timeout, JSON parse error, etc.) — use **Reprocess Failed** |

---

## Clearing All Data (Fresh Re-index)

### PostgreSQL
```sql
TRUNCATE TABLE lightrag_doc_status, lightrag_doc_chunks,
               lightrag_llm_cache, lightrag_llm_response_cache,
               "LIGHTRAG_VDB_ENTITY_nomic_embed_text_768d",
               "LIGHTRAG_VDB_RELATION_nomic_embed_text_768d",
               "LIGHTRAG_VDB_CHUNKS_nomic_embed_text_768d"
CASCADE;
```

### Neo4j
```cypher
// Run repeatedly until 0 nodes deleted
MATCH (n) WITH n LIMIT 10000 DETACH DELETE n
```
