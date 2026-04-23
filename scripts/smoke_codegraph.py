"""Live smoke test for codegraph ingestion against real LightRAG storage.

Spins up a LightRAG instance using the default local storages (NetworkX
graph, nano-vectordb vector storage, JSON KV), ingests a handful of .py
files from this repo via ``ingest_code_file``, and verifies the graph +
vector store + manifest look right.

Deliberately NOT exercising the full ``apipeline_process_enqueue_documents``
path — that needs a working embedding model and LLM. The point here is
to validate the storage-layer I/O of the ``ingest_code_file`` bridge,
which is the path Phase 2b wires the pipeline through.

Run:
    .venv/Scripts/python scripts/smoke_codegraph.py
or (from repo root):
    python -m scripts.smoke_codegraph
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

# Ensure the project root is importable.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Flag must be on before LightRAG is imported (dataclass default reads env).
os.environ.setdefault("CODE_GRAPH_ENABLED", "true")

from lightrag import LightRAG
from lightrag.codegraph.ingest import ingest_code_file, purge_file
from lightrag.utils import EmbeddingFunc


EMBED_DIM = 64


async def _stub_embed(texts):
    """Deterministic fake embedding — hash(text) → seeded RNG → 64-dim float32."""
    out = np.empty((len(texts), EMBED_DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        seed = hash(t) & 0xFFFFFFFF
        out[i] = np.random.RandomState(seed).normal(size=EMBED_DIM).astype(np.float32)
    return out


async def _stub_llm(*_args, **_kwargs):
    raise AssertionError(
        "stub LLM was invoked — codegraph should bypass LLM for code files"
    )


SAMPLE_FILES = [
    "lightrag/codegraph/_python.py",
    "lightrag/codegraph/_base.py",
    "lightrag/codegraph/ingest.py",
]


async def main() -> int:
    print("=" * 70)
    print("Codegraph live smoke test")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="codegraph_smoke_") as td:
        print(f"working_dir: {td}")
        print(f"code_graph_enabled env: {os.environ.get('CODE_GRAPH_ENABLED')}")
        print()

        rag = LightRAG(
            working_dir=td,
            llm_model_func=_stub_llm,
            embedding_func=EmbeddingFunc(
                embedding_dim=EMBED_DIM,
                max_token_size=8192,
                func=_stub_embed,
            ),
            code_graph_enabled=True,
        )
        await rag.initialize_storages()
        print(f"code_graph_enabled on instance: {rag.code_graph_enabled}")
        print()

        try:
            # --- 1. Ingest a few Python files -----------------------------
            print("--- 1. Ingesting sample files ---")
            total = {"nodes": 0, "edges": 0, "purged_nodes": 0, "embedded": 0}
            for rel in SAMPLE_FILES:
                src = (REPO / rel).read_text(encoding="utf-8")
                counts = await ingest_code_file(rag, rel, src)
                print(
                    f"  {rel:45s}  "
                    f"{counts['nodes']:3d} nodes  "
                    f"{counts['edges']:4d} edges  "
                    f"{counts['embedded']:2d} embedded  "
                    f"{counts['purged_nodes']:2d} purged"
                )
                for k in total:
                    total[k] += counts[k]
            print(f"  {'TOTAL':45s}  {total['nodes']:3d} nodes  {total['edges']:4d} edges  {total['embedded']:2d} embedded")
            print()

            # --- 2. Inspect the graph storage -----------------------------
            print("--- 2. Graph storage contents ---")
            graph = rag.chunk_entity_relation_graph
            all_labels = await graph.get_all_labels()
            print(f"  distinct node_ids in graph: {len(all_labels)}")

            # Count by entity_type
            from collections import Counter
            type_counts: Counter[str] = Counter()
            for label in all_labels:
                n = await graph.get_node(label)
                if n:
                    type_counts[n.get("entity_type", "?")] += 1
            for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
                print(f"    {t:20s} {n}")
            print()

            # --- 3. Spot-check a known symbol -----------------------------
            print("--- 3. Spot-check known symbols ---")
            want = "py:lightrag.codegraph._python.extract"
            node = await graph.get_node(want)
            if node is None:
                print(f"  FAIL: node {want!r} not in graph")
                return 1
            print(f"  OK: {want}")
            print(f"    entity_type: {node.get('entity_type')}")
            print(f"    qualified_name: {node.get('qualified_name')}")
            print(f"    file_path: {node.get('file_path')}")
            print(f"    source_id: {node.get('source_id')}")
            print(f"    lines: {node.get('line_start')}-{node.get('line_end')}")
            print(f"    signature: {node.get('signature', '')[:80]}")
            print()

            # --- 4. Inspect edges from that node --------------------------
            print("--- 4. Outbound edges from py:...extract ---")
            edges = await graph.get_node_edges(want)
            if not edges:
                print(f"  FAIL: no edges found for {want}")
                return 1
            print(f"  edge count: {len(edges)}")
            for src_id, tgt_id in edges[:10]:
                edge = await graph.get_edge(src_id, tgt_id)
                if edge:
                    rel = edge.get("relation", "?")
                    print(f"    {rel:10s}  {src_id[:40]:40s} -> {tgt_id}")
            print()

            # --- 5. Entity VDB rows ---------------------------------------
            print("--- 5. Entity vdb ---")
            vdb_path = Path(td) / "vdb_entities.json"
            if vdb_path.exists():
                vdb_data = json.loads(vdb_path.read_text())
                rows = vdb_data.get("data", [])
                print(f"  rows in entities_vdb: {len(rows)}")
                if rows:
                    sample = rows[0]
                    print(f"  sample row keys: {sorted(sample.keys())}")
                    print(f"    entity_name: {sample.get('entity_name')}")
                    print(f"    file_path:   {sample.get('file_path')}")
                    print(f"    content[:80]: {str(sample.get('content', ''))[:80]!r}")
            else:
                print(f"  (vdb_entities.json not found at {vdb_path})")
            print()

            # --- 6. Manifest ---------------------------------------------
            print("--- 6. codegraph manifest ---")
            mf = Path(td) / "codegraph_manifest.json"
            if not mf.exists():
                print(f"  FAIL: manifest not written at {mf}")
                return 1
            manifest = json.loads(mf.read_text())
            print(f"  files tracked: {len(manifest)}")
            for f, ids in manifest.items():
                print(f"    {f}: {len(ids)} node_ids")
            print()

            # --- 7. Re-ingest one file (should purge first) ----------------
            print("--- 7. Re-ingest with a simulated edit ---")
            target = "lightrag/codegraph/_python.py"
            orig_src = (REPO / target).read_text(encoding="utf-8")
            # Chop off the last function definition to simulate a delete.
            trimmed = orig_src.rsplit("def _emit_call", 1)[0]
            counts = await ingest_code_file(rag, target, trimmed)
            print(
                f"  re-ingest {target}: "
                f"{counts['nodes']} new nodes, "
                f"{counts['edges']} new edges, "
                f"{counts['purged_nodes']} purged"
            )
            # _emit_call should no longer exist in the graph
            dropped = "py:lightrag.codegraph._python._emit_call"
            n = await graph.get_node(dropped)
            if n is not None:
                print(f"  FAIL: stale symbol {dropped!r} was not purged")
                return 1
            print(f"  OK: stale symbol {dropped!r} removed from graph")
            print()

            # --- 8. purge_file on deleted file ----------------------------
            print("--- 8. purge_file on a removed file ---")
            before = len(await graph.get_all_labels())
            purged = await purge_file(rag, "lightrag/codegraph/_base.py")
            after = len(await graph.get_all_labels())
            print(f"  purged {purged} nodes; graph went {before} -> {after}")
            if after >= before:
                print("  FAIL: node count did not shrink after purge")
                return 1
            print("  OK: purge dropped nodes from graph")
            print()

            print("=" * 70)
            print("SMOKE PASSED")
            print("=" * 70)
            return 0
        finally:
            await rag.finalize_storages()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
