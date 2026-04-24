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
async def find_callers(target: str) -> str:
    """Find all symbols with a ``calls`` edge pointing AT *target*.

    Deterministic graph walk — no LLM, millisecond-scale. Use whenever
    a query is about "who invokes X" / "where is X used".

    Args:
        target: Fully-qualified node id, e.g.
            ``py:lightrag.codegraph._python.extract`` or
            ``java:com.coupons.auth.Dog``. Find candidates first via
            ``search`` or ``get_symbol`` — prefix convention:
            ``py:`` / ``ts:`` / ``js:`` / ``java:`` / ``cs:``.

    Returns:
        Markdown table of (source, qualified_name, entity_type,
        file_path, line) for each caller. Empty if none found (often
        a sign the target id isn't fully qualified — try the short
        name via ``search`` first).
    """
    return await _graph_list_query("callers", target)


@mcp.tool()
async def find_implementers(target: str) -> str:
    """Find all symbols with an ``inherits`` edge pointing AT *target*.

    Covers both subclass (``extends``) and interface-implementation
    (``implements``) edges — both relations collapse to ``inherits``.

    Args:
        target: Fully-qualified node id of a class or interface.

    Returns:
        Markdown table of implementers/subclasses.
    """
    return await _graph_list_query("implementers", target)


@mcp.tool()
async def find_importers(target: str) -> str:
    """Find all symbols / files with an ``imports`` edge pointing AT *target*.

    Target is typically a module — ``py:requests``, ``ts:./utils``,
    ``java:java.util.List``. Useful for blast-radius analysis before
    renaming or removing a module.

    Args:
        target: The imported module id.

    Returns:
        Markdown table of importers.
    """
    return await _graph_list_query("importers", target)


@mcp.tool()
async def get_symbol(fqn: str) -> str:
    """Full detail for one symbol: node attributes + split incident edges.

    Returns the node's entity_type, qualified_name, file_path,
    line_start/end, plus *outgoing* edges (what it calls / inherits /
    imports) and *incoming* edges (who calls / implements / imports it).
    Combines what ``find_callers`` / ``find_implementers`` / etc. show
    into one compact view for a specific node.

    Args:
        fqn: Fully-qualified node id, e.g.
            ``py:lightrag.codegraph._python.extract``.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{_base_url()}/graph/code/symbol",
            params={"fqn": fqn},
            headers=_headers(),
        )
        if resp.status_code == 404:
            return f"No symbol with fqn `{fqn}` in the graph."
        resp.raise_for_status()
        detail = resp.json()

    node = detail.get("node", {}) or {}
    lines: list[str] = []
    lines.append(f"## `{fqn}`")
    if detail.get("is_stub"):
        lines.append("*(stub node — placeholder created by an edge; not fully indexed)*")
    lines.append("")
    lines.append(f"- entity_type: `{node.get('entity_type', '-')}`")
    lines.append(f"- qualified_name: `{node.get('qualified_name', '-')}`")
    if fp := node.get("file_path"):
        lines.append(f"- file: `{fp}:{node.get('line_start', '?')}-{node.get('line_end', '?')}`")
    if sig := node.get("signature"):
        lines.append(f"- signature: `{sig}`")

    if outgoing := detail.get("outgoing"):
        lines.append("")
        lines.append(f"### Outgoing ({len(outgoing)})")
        for e in outgoing:
            marker = " *(unresolved)*" if e.get("unresolved") else ""
            kind = f" ({e['kind']})" if e.get("kind") else ""
            lines.append(f"- **{e['relation']}**{kind} → `{e['other']}`{marker}  [`{e.get('file_path', '?')}:{e.get('line', '?')}`]")
    if incoming := detail.get("incoming"):
        lines.append("")
        lines.append(f"### Incoming ({len(incoming)})")
        for e in incoming:
            marker = " *(unresolved)*" if e.get("unresolved") else ""
            kind = f" ({e['kind']})" if e.get("kind") else ""
            lines.append(f"- **{e['relation']}**{kind} ← `{e['other']}`{marker}  [`{e.get('file_path', '?')}:{e.get('line', '?')}`]")

    return "\n".join(lines)


async def _graph_list_query(endpoint: str, target: str) -> str:
    """Shared transport for find_callers / find_implementers / find_importers.

    Maps to ``GET /graph/code/{endpoint}?target=<target>`` and renders a
    compact markdown table. Keeps the three tool functions above tiny.
    """
    result_key = {
        "callers": "callers",
        "implementers": "implementers",
        "importers": "importers",
    }[endpoint]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{_base_url()}/graph/code/{endpoint}",
            params={"target": target},
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    hits = data.get(result_key, []) or []
    if not hits:
        return f"No {result_key} found for `{target}`."

    lines = [f"## {len(hits)} {result_key} of `{target}`", ""]
    lines.append("| source | qualified_name | entity_type | file:line |")
    lines.append("|---|---|---|---|")
    for h in hits:
        lines.append(
            f"| `{h.get('source', '?')}` "
            f"| `{h.get('qualified_name', '-') or '-'}` "
            f"| {h.get('entity_type', '-') or '-'} "
            f"| `{h.get('file_path', '?')}:{h.get('line', '?')}` |"
        )
    return "\n".join(lines)


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
    counts = counts_resp.json().get("status_counts") or {}
    pipeline = pipeline_resp.json() or {}

    lines = ["## Document counts"]
    if counts:
        for status_name, n in sorted(counts.items(), key=lambda x: -x[1]):
            lines.append(f"- **{status_name}**: {n}")
    else:
        lines.append("- (empty)")

    lines.append("")
    lines.append("## Pipeline")
    lines.append(f"- busy: {pipeline.get('busy', False)}")
    if msg := pipeline.get("latest_message"):
        lines.append(f"- latest: {msg}")
    if cur := pipeline.get("cur_batch"):
        total = pipeline.get("batchs", "?")
        lines.append(f"- progress: {cur}/{total}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
