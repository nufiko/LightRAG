"""Shared helpers for file-type-aware chunking."""

from __future__ import annotations

import re
from typing import Any

from lightrag.utils import Tokenizer


def split_on_pattern(content: str, pattern: re.Pattern[str]) -> list[str]:
    """Split *content* at every zero-width match of *pattern*.

    The pattern must use a look-ahead so that m.start() points to the
    character just before the logical boundary.  Empty segments are dropped.
    """
    positions = [0] + [m.start() for m in pattern.finditer(content)]
    segments: list[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(content)
        seg = content[pos:end]
        if seg.strip():
            segments.append(seg)
    return segments


def pack_segments(
    tokenizer: Tokenizer,
    segments: list[str],
    chunk_token_size: int,
    chunk_overlap_token_size: int,
) -> list[dict[str, Any]]:
    """Pack text segments into token-bounded chunks.

    Segments smaller than *chunk_token_size* are greedily merged so output
    chunks approach the token budget.  Oversized segments are sub-split with
    the standard token-overlap algorithm.
    """
    results: list[str] = []
    current_parts: list[str] = []
    current_tokens: int = 0

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue

        tok = tokenizer.encode(seg)
        tok_count = len(tok)
        if tok_count == 0:
            continue

        if tok_count > chunk_token_size:
            if current_parts:
                results.append("\n\n".join(current_parts))
                current_parts = []
                current_tokens = 0
            for start in range(0, tok_count, chunk_token_size - chunk_overlap_token_size):
                piece = tokenizer.decode(tok[start : start + chunk_token_size])
                if piece.strip():
                    results.append(piece.strip())
        elif current_tokens + tok_count > chunk_token_size:
            results.append("\n\n".join(current_parts))
            current_parts = [seg]
            current_tokens = tok_count
        else:
            current_parts.append(seg)
            current_tokens += tok_count

    if current_parts:
        results.append("\n\n".join(current_parts))

    return [
        {
            "tokens": len(tokenizer.encode(c)),
            "content": c,
            "chunk_order_index": i,
        }
        for i, c in enumerate(results)
    ]
