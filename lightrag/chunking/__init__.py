"""File-type-aware chunking for LightRAG.

Each language module in this package registers itself via EXTENSIONS and
get_pattern().  The dispatcher in chunking_by_file_type() picks the right
module by file extension and falls back to token-based chunking when no
module matches or when the pattern finds no split points.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lightrag.operate import chunking_by_token_size
from lightrag.utils import Tokenizer

from lightrag.chunking._base import pack_segments, split_on_pattern
from lightrag.chunking import (
    _csharp,
    _go,
    _java,
    _javascript,
    _markdown,
    _powershell,
    _python,
)

# Registry: extension → language module (must expose get_pattern(content))
_REGISTRY: dict[str, Any] = {}
for _mod in (_csharp, _go, _java, _javascript, _markdown, _powershell, _python):
    for _ext in _mod.EXTENSIONS:
        _REGISTRY[_ext] = _mod


def chunking_by_file_type(
    tokenizer: Tokenizer,
    content: str,
    split_by_character: str | None = None,
    split_by_character_only: bool = False,
    chunk_overlap_token_size: int = 100,
    chunk_token_size: int = 1200,
    file_path: str | None = None,
    **kwargs,
) -> list[dict[str, Any]]:
    """File-type-aware chunking entry point for LightRAG.

    Dispatches to the appropriate language module based on the file extension
    from *file_path*, then packs the resulting segments into token-bounded
    chunks.  Falls back to default token-based chunking when:
    - the caller supplies an explicit *split_by_character*
    - no language module is registered for the extension
    - the pattern finds no useful split points in the file
    """
    if split_by_character is not None:
        return chunking_by_token_size(
            tokenizer,
            content,
            split_by_character,
            split_by_character_only,
            chunk_overlap_token_size,
            chunk_token_size,
        )

    ext = Path(file_path).suffix.lower() if file_path else ""
    module = _REGISTRY.get(ext)

    if module is None:
        return chunking_by_token_size(
            tokenizer,
            content,
            split_by_character,
            split_by_character_only,
            chunk_overlap_token_size,
            chunk_token_size,
        )

    pattern: re.Pattern[str] | None = module.get_pattern(content)
    if pattern is None:
        return chunking_by_token_size(
            tokenizer,
            content,
            split_by_character,
            split_by_character_only,
            chunk_overlap_token_size,
            chunk_token_size,
        )

    segments = split_on_pattern(content, pattern)
    if len(segments) <= 1:
        return chunking_by_token_size(
            tokenizer,
            content,
            split_by_character,
            split_by_character_only,
            chunk_overlap_token_size,
            chunk_token_size,
        )

    return pack_segments(tokenizer, segments, chunk_token_size, chunk_overlap_token_size)


__all__ = ["chunking_by_file_type"]
