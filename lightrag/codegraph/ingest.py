"""Ingest a single code file: extract symbols, write to graph + vector storage.

This module is the Phase 2 bridge between the deterministic tree-sitter
extractor (``lightrag.codegraph._python`` etc.) and the LightRAG storage
layer. It bypasses the LLM entity-extraction path entirely for code files.

Stale-symbol handling: every re-ingest of a file purges the nodes/edges
emitted for that file on the previous pass before inserting fresh ones.
A per-workspace manifest at ``<working_dir>/codegraph_manifest.json``
tracks which node ids belong to which file, so purges are O(1) per file
and portable across storage backends.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lightrag.codegraph import get_extractor
from lightrag.codegraph._base import (
    NODE_CLASS,
    NODE_FILE,
    NODE_FUNCTION,
    NODE_MODULE,
    CodeEdge,
    CodeNode,
    edge_to_storage,
    node_to_storage,
)
from lightrag.utils import compute_mdhash_id, logger

if TYPE_CHECKING:
    from lightrag import LightRAG


# Symbol types whose body is worth embedding for retrieval. Module and file
# nodes don't have meaningful bodies — skip them to save embedding budget.
_EMBEDDABLE_TYPES = {NODE_CLASS, NODE_FUNCTION}

_MANIFEST_FILENAME = "codegraph_manifest.json"


def is_code_file(file_path: str) -> bool:
    """True iff a code extractor is registered for this extension."""
    return get_extractor(file_path) is not None


async def ingest_code_file(
    rag: "LightRAG",
    file_path: str,
    source: str,
) -> dict[str, int]:
    """Extract symbols from *source* and upsert into *rag*'s storage.

    Args:
        rag: Initialized LightRAG instance (``await rag.initialize_storages()``
            must have already run).
        file_path: Repo-relative path; used as provenance and manifest key.
        source: File contents (UTF-8).

    Returns:
        Counts of work done: ``{"nodes", "edges", "purged_nodes", "embedded"}``.
    """
    extractor = get_extractor(file_path)
    if extractor is None:
        logger.debug(f"codegraph: no extractor for {file_path}, skipping")
        return {"nodes": 0, "edges": 0, "purged_nodes": 0, "embedded": 0}

    nodes, edges = extractor.extract(source, file_path)

    # Purge previous pass for this file (stale-symbol invariant).
    manifest_path = Path(rag.working_dir) / _MANIFEST_FILENAME
    manifest = _load_manifest(manifest_path)
    purged = await _purge_file(rag, file_path, manifest)

    # Upsert graph nodes + edges in batch.
    if nodes:
        await rag.chunk_entity_relation_graph.upsert_nodes_batch(
            [node_to_storage(n) for n in nodes]
        )
    if edges:
        await rag.chunk_entity_relation_graph.upsert_edges_batch(
            [edge_to_storage(e) for e in edges]
        )

    # Upsert vector embeddings for class/function symbols.
    embedded = await _upsert_entity_vectors(rag, nodes, source)

    # Record this file's node ids in the manifest for next-time purge.
    manifest[file_path] = [n.node_id for n in nodes]
    _save_manifest(manifest_path, manifest)

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "purged_nodes": purged,
        "embedded": embedded,
    }


async def purge_file(rag: "LightRAG", file_path: str) -> int:
    """Drop all symbols previously ingested from *file_path*.

    Use when a file is deleted from the repo.
    """
    manifest_path = Path(rag.working_dir) / _MANIFEST_FILENAME
    manifest = _load_manifest(manifest_path)
    purged = await _purge_file(rag, file_path, manifest)
    manifest.pop(file_path, None)
    _save_manifest(manifest_path, manifest)
    return purged


# --- Internals --------------------------------------------------------------


async def _purge_file(
    rag: "LightRAG",
    file_path: str,
    manifest: dict[str, list[str]],
) -> int:
    """Delete all nodes previously recorded for *file_path*.

    Deleting a node in ``BaseGraphStorage`` removes its incident edges in all
    supported backends, so we don't need to track edge ids separately.
    Entity-vdb rows are removed via the same node-id key.
    """
    stale_ids = manifest.get(file_path, [])
    if not stale_ids:
        return 0

    # Graph storage: delete by node id.
    for node_id in stale_ids:
        try:
            await rag.chunk_entity_relation_graph.delete_node(node_id)
        except Exception as e:  # pragma: no cover - backend-specific
            logger.warning(f"codegraph: purge delete_node({node_id}) failed: {e}")

    # Vector storage: hashed ids follow the same convention as the fresh
    # upsert, so deleting by that set cleans up stale rows.
    vdb_ids = [compute_mdhash_id(nid, prefix="ent-") for nid in stale_ids]
    try:
        await rag.entities_vdb.delete(vdb_ids)
    except Exception as e:  # pragma: no cover
        logger.warning(f"codegraph: purge entities_vdb.delete failed: {e}")

    return len(stale_ids)


async def _upsert_entity_vectors(
    rag: "LightRAG",
    nodes: list[CodeNode],
    source: str,
) -> int:
    """Embed symbol bodies into the entities vdb.

    Only classes and functions are embedded (modules / files have no
    meaningful body). Content is ``qualified_name + "\\n" + body_text`` to
    give the embedder signal beyond just the name.
    """
    lines = source.splitlines()
    payload: dict[str, dict[str, Any]] = {}

    for node in nodes:
        if node.entity_type not in _EMBEDDABLE_TYPES:
            continue
        body = "\n".join(lines[node.line_start - 1 : node.line_end])
        content = f"{node.qualified_name}\n{body}"
        payload[compute_mdhash_id(node.node_id, prefix="ent-")] = {
            "content": content,
            "entity_name": node.node_id,
            "entity_type": node.entity_type,
            "description": node.description or node.signature,
            "source_id": node.source_id(),
            "file_path": node.file_path,
        }

    if payload:
        await rag.entities_vdb.upsert(payload)
    return len(payload)


def _load_manifest(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"codegraph: manifest load failed ({e}); starting fresh")
        return {}


def _save_manifest(path: Path, manifest: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


__all__ = ["ingest_code_file", "is_code_file", "purge_file"]
