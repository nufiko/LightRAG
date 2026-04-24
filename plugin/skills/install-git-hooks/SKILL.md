---
name: install-git-hooks
description: Install git hooks in the current repo so the LightRAG index auto-updates after git pull, merge, checkout, rebase, or commit. One-time setup per repo. Run this after adding the plugin to a new project.
---

# Install LightRAG git hooks

Writes four hook scripts into `.git/hooks/` so the LightRAG index stays
current without manual `/lightrag:scan` calls.

## Hooks installed

| Hook | Fires after |
|---|---|
| `post-merge` | `git pull`, `git merge` |
| `post-checkout` | `git checkout`, `git switch` (branch change) |
| `post-commit` | `git commit` |
| `post-rewrite` | `git rebase`, `git commit --amend` |

Each hook fires a background scan request and returns immediately — git
is never blocked even if LightRAG is down.

## What to do

1. Find the repo root by running:
   ```
   git rev-parse --show-toplevel
   ```

2. For each of the four hooks (`post-merge`, `post-checkout`, `post-commit`,
   `post-rewrite`), write this content to `.git/hooks/<hook-name>`:

   ```sh
   #!/bin/sh
   curl -s --max-time 2 -X POST "${LIGHTRAG_URL:-http://localhost:9621}/documents/scan" || true
   ```

   If the hook file already exists and does not contain a `lightrag` line,
   **append** the curl line rather than overwriting — preserve existing hooks.
   If it already contains the curl line, skip (idempotent).

3. Make each hook executable:
   ```
   chmod +x .git/hooks/post-merge .git/hooks/post-checkout .git/hooks/post-commit .git/hooks/post-rewrite
   ```

4. Report which hooks were created, which were appended to, and which were
   already present. Finish with a one-line confirmation:
   > "Git hooks installed — the LightRAG index will update automatically after pull, checkout, commit, and rebase."

## When to invoke

- User says: *"install git hooks", "set up auto-scan", "make the index update on pull"*.
- After a fresh plugin install on a new repo.

## When NOT to invoke

- Without being asked.
- If `.git/` is not present (not a git repo) — tell the user instead.
