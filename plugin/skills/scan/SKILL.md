---
name: scan
description: Trigger a re-scan of the LightRAG server's INPUT_DIR so the index picks up new or changed files. Returns immediately; scanning runs in the background. Use only when the user explicitly asks to refresh the index, or after they've mentioned making a batch of changes that should be re-indexed.
---

# Re-index the codebase

Call **`mcp__lightrag__scan`** to trigger a re-scan of the server's configured `INPUT_DIR`.

## When to invoke

- User says: *"re-index", "rescan", "refresh the index", "pick up my changes"*.
- After the user describes a batch of changes they want searchable: *"I just pulled the latest main, can you rescan?"*
- After a large refactor where search results will be stale.

## When NOT to invoke

- After a single edit you just made — the server's git hooks (if installed) or the user's own workflow handles that.
- Without being asked — scanning is cheap but not free (LLM time on prose files, embedding time on chunks). Don't auto-trigger.

## Output

Returns immediately with a confirmation message. Scanning continues in the background — call the **`status`** tool to observe progress.

## Typical flow

1. User: "Can you rescan my code?"
2. You call `scan`.
3. You optionally call `status` once to confirm `busy: true` and report progress.
4. Continue with other work. Poll `status` later if the user wants confirmation of completion.
