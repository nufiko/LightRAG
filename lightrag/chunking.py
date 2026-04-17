"""File-type-aware chunking for LightRAG.

Applies different split strategies based on file extension so that
logical code units (methods, headings, functions) stay intact across
chunk boundaries instead of being cut at arbitrary token offsets.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lightrag.operate import chunking_by_token_size
from lightrag.utils import Tokenizer


# ---------------------------------------------------------------------------
# Compiled split-boundary patterns
# ---------------------------------------------------------------------------

# C#: blank line immediately before an optional run of attributes then an
# access/modifier keyword — keeps method/property declarations with their body.
_CS_BOUNDARY = re.compile(
    r"(?=\n[ \t]*\n"
    r"(?:[ \t]*\[[^\]\n]+\][ \t]*\n)*"
    r"[ \t]+"
    r"(?:public|private|protected|internal|static|override|abstract|virtual|sealed)\b)",
    re.MULTILINE,
)

# AngularJS: split before each $scope.xxx = assignment.
_JS_ANGULARJS_BOUNDARY = re.compile(
    r"(?=\n[ \t]*\$scope\.\w+[ \t]*=)",
    re.MULTILINE,
)

# Modern JS/TS: split before top-level function declarations and common
# function-expression assignments.
_JS_FUNCTION_BOUNDARY = re.compile(
    r"(?=\n[ \t]*"
    r"(?:"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+"  # function declarations
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?function"  # var = function
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\("  # var = async (...) =>
    r"|\w+(?:\.\w+)+\s*=\s*(?:async\s+)?function"  # obj.method = function
    r"))",
    re.MULTILINE,
)

# Markdown: split before any level-1/2/3 heading.
_MD_HEADING = re.compile(r"(?=\n#{1,3} )", re.MULTILINE)

# PowerShell: split before each function declaration.
_PS1_FUNCTION = re.compile(r"(?=\nfunction\s+)", re.MULTILINE | re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_on_pattern(content: str, pattern: re.Pattern[str]) -> list[str]:
    """Split *content* at every zero-width match of *pattern*.

    The pattern must use a look-ahead so that m.start() points to the
    character just before the logical boundary (the trailing newline of the
    preceding block).  Each resulting segment is non-empty after stripping.
    """
    positions = [0] + [m.start() for m in pattern.finditer(content)]
    segments: list[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(content)
        seg = content[pos:end]
        if seg.strip():
            segments.append(seg)
    return segments


def _pack_segments(
    tokenizer: Tokenizer,
    segments: list[str],
    chunk_token_size: int,
    chunk_overlap_token_size: int,
) -> list[dict[str, Any]]:
    """Pack text segments into token-bounded chunks.

    Segments smaller than *chunk_token_size* are greedily merged (joined with
    a blank line) so output chunks approach the token budget.  Segments that
    exceed the budget are sub-split with the standard token-overlap algorithm.
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
            # Flush accumulator before handling the oversized segment.
            if current_parts:
                results.append("\n\n".join(current_parts))
                current_parts = []
                current_tokens = 0
            # Sub-split with overlap to stay within the token budget.
            for start in range(
                0, tok_count, chunk_token_size - chunk_overlap_token_size
            ):
                piece = tokenizer.decode(tok[start : start + chunk_token_size])
                if piece.strip():
                    results.append(piece.strip())
        elif current_tokens + tok_count > chunk_token_size:
            # Current accumulator is full — flush and start a new one.
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


# ---------------------------------------------------------------------------
# Public chunking function
# ---------------------------------------------------------------------------


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
    """File-type-aware chunking function for LightRAG.

    Chooses a split strategy based on the file extension extracted from
    *file_path*, then packs the resulting segments into token-bounded chunks.

    Supported strategies:
    - ``.cs``   — split on C# member boundaries (blank line + access modifier)
    - ``.js``   — split on ``$scope.`` assignments (AngularJS) or function
                  declarations (modern JS)
    - ``.ts``   — same as ``.js``
    - ``.md``   — split on level-1/2/3 Markdown headings
    - ``.ps1``  — split on PowerShell ``function`` declarations
    - anything  — fall through to the default token-based chunking

    If the caller supplies an explicit *split_by_character* that value is
    honoured unconditionally (matches the existing LightRAG contract).
    """
    # Honour an explicit caller-supplied split character.
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

    if ext == ".cs":
        pattern = _CS_BOUNDARY
    elif ext in (".js", ".ts"):
        # Prefer $scope-based splits for AngularJS files; fall back to
        # general function-declaration splits for modern JS/TS.
        pattern = (
            _JS_ANGULARJS_BOUNDARY
            if "$scope." in content
            else _JS_FUNCTION_BOUNDARY
        )
    elif ext == ".md":
        pattern = _MD_HEADING
    elif ext == ".ps1":
        pattern = _PS1_FUNCTION
    else:
        return chunking_by_token_size(
            tokenizer,
            content,
            split_by_character,
            split_by_character_only,
            chunk_overlap_token_size,
            chunk_token_size,
        )

    segments = _split_on_pattern(content, pattern)

    # If the pattern produced no useful split points (e.g. the file has no
    # matching markers), fall back to default token-based chunking.
    if len(segments) <= 1:
        return chunking_by_token_size(
            tokenizer,
            content,
            split_by_character,
            split_by_character_only,
            chunk_overlap_token_size,
            chunk_token_size,
        )

    return _pack_segments(
        tokenizer, segments, chunk_token_size, chunk_overlap_token_size
    )
