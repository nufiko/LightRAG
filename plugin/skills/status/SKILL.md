---
name: status
description: Show the LightRAG server's indexing state — counts of documents in each status (pending, embedded, processed, failed) plus live pipeline progress. Use to check whether an indexing run is still in progress, what proportion of files have succeeded, or when the user asks "how's the index looking".
---

# Check LightRAG status

Call **`mcp__lightrag__status`** to read the current indexing state.

## When to invoke

- After calling `scan` — confirm work started, watch progress.
- When the user asks *"how's the indexing going?"*, *"is it done yet?"*, *"how many files?"*
- Before a broad `query` call — if there are many `pending` or `failed` docs, the answer may miss things; mention that caveat.

## Output (compact markdown)

```
## Document counts
- processed: 557
- pending: 6978
- embedded: 4
- failed: 81

## Pipeline
- busy: true
- latest: Embedded 1234/9810: vouchercloud/...
- progress: 1234/9810
```

## Reading the output

- **busy: true** — a scan or pipeline run is actively processing.
- **embedded** — docs whose chunks are embedded but graph extraction hasn't finished (normally small; large numbers mean LLM extraction is bottlenecked).
- **failed** — extraction failed for these docs. User can reprocess via the WebUI's "Reprocess Failed" button or the corresponding REST endpoint.
- **processed** — fully indexed and queryable.

If a broad `query` is about to be issued and many docs are still `pending`/`embedded`, mention to the user that coverage may be incomplete.
