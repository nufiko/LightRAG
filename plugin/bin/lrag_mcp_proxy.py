"""MCP proxy for the LightRAG Claude Code plugin.

Exposes three tools over stdio to a local ``lightrag-server``:

- ``search`` — hit ``POST /query`` for semantic retrieval + LLM answer
- ``scan``   — hit ``POST /documents/scan`` to trigger a re-scan of INPUT_DIR
- ``status`` — hit ``GET /documents/status_counts`` + ``/pipeline_status``
               for doc counts and live pipeline state

Configured via environment (set in ``.mcp.json`` or the user's shell):

- ``LIGHTRAG_URL``       — base URL, default ``http://127.0.0.1:9621``
- ``LIGHTRAG_API_KEY``   — optional bearer token

This file is invoked by Claude Code via ``.mcp.json`` with ``uv run``, which
pulls the ``mcp`` and ``httpx`` deps on first run.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("lightrag")


def _base_url() -> str:
    return os.environ.get("LIGHTRAG_URL", "http://127.0.0.1:9621").rstrip("/")


def _headers() -> dict[str, str]:
    api_key = os.environ.get("LIGHTRAG_API_KEY", "").strip()
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


@mcp.tool()
async def search(
    query: str,
    mode: str = "hybrid",
    top_k: int = 20,
    only_context: bool = False,
) -> str:
    """Semantic + graph search across the indexed codebase.

    Best for questions Glob/Grep can't answer cheaply:
    - "where is X defined", "who calls Y", "what does Z do"
    - architecture / cross-file questions
    - fuzzy-match queries when exact names are unknown

    Args:
        query: Natural-language question or keyword search.
        mode: One of ``local`` / ``global`` / ``hybrid`` / ``naive`` / ``mix``.
            Default ``hybrid`` is a good general-purpose balance; ``mix``
            is best when a reranker is configured server-side.
        top_k: Number of entities / relations to pull from the graph.
            Default 20 trades recall for prompt size.
        only_context: If True, skip the LLM answer and return raw retrieved
            context blocks (chunks + entity descriptions + citations). Useful
            when Claude wants to reason over the raw material itself.

    Returns:
        The LLM-synthesized answer (default) or the raw context block
        (when ``only_context=True``). Citations are inline when
        the server's prompt template enables them.
    """
    payload: dict = {
        "query": query,
        "mode": mode,
        "top_k": top_k,
        "only_need_context": only_context,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{_base_url()}/query", json=payload, headers=_headers()
        )
        resp.raise_for_status()
        data = resp.json()
    return data.get("response", "") or data.get("data", "") or str(data)


@mcp.tool()
async def scan() -> str:
    """Trigger a re-scan of the server's INPUT_DIR.

    Returns immediately — scanning runs in the background. Use
    ``status`` to observe progress.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_base_url()}/documents/scan", headers=_headers()
        )
        resp.raise_for_status()
    return (
        "Scan triggered. Call `status` to watch progress — the busy "
        "flag flips False when indexing finishes."
    )


@mcp.tool()
async def status() -> str:
    """Show current indexing state: doc counts by status + pipeline info.

    Returns a compact markdown summary rather than raw JSON so it reads
    cleanly in the Claude Code transcript.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        counts_resp = await client.get(
            f"{_base_url()}/documents/status_counts", headers=_headers()
        )
        pipeline_resp = await client.get(
            f"{_base_url()}/documents/pipeline_status", headers=_headers()
        )
    counts_resp.raise_for_status()
    pipeline_resp.raise_for_status()
    counts = counts_resp.json() or {}
    pipeline = pipeline_resp.json() or {}

    lines = ["## Document counts"]
    if counts:
        for status_name, n in sorted(counts.items(), key=lambda x: -int(x[1])):
            lines.append(f"- **{status_name}**: {n}")
    else:
        lines.append("- (empty)")

    lines.append("")
    lines.append("## Pipeline")
    lines.append(f"- busy: {pipeline.get('busy', False)}")
    if msg := pipeline.get("latest_message"):
        lines.append(f"- latest: {msg}")
    if cur := pipeline.get("cur"):
        total = pipeline.get("total", "?")
        lines.append(f"- progress: {cur}/{total}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
