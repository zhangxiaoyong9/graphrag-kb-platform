# Verify — A3 doc-delete graph-shrink

Date: 2026-06-28
Branch: `feat/a3-doc-delete-graph-shrink`
Scope: Full backend + frontend regression for the A3 feature ("deleting a document
converges — the graph shrinks"). Tasks 1–5 (the implementation) were reviewed
individually with focused suites; this record is the authoritative full-suite run.

## What A3 shipped

1. **`merge_delta` filters extractions by the live chunk table + best-effort prunes
   orphan `extractions/*.json`** — the root cause of the "graph doesn't shrink"
   bug. Old code globbed every `extractions/*.json` on disk, so deleted chunks'
   entities were re-merged on every incremental run. (`engine/incremental.py`)
2. **`write_text_units_parquet` writes a zero-row, column-correct parquet** when
   there are no live chunks, so deleting the last document yields a well-formed
   empty graph instead of a missing/empty-file error. (`engine/incremental.py`)
3. **DELETE `/kbs/{id}/documents/{doc_id}` auto-creates a coalesced incremental
   shrink job** when the KB has been indexed: returns `202` + `JobCreated` for a
   previously-indexed KB (and `204` when there is nothing to shrink). A coalescing
   rule avoids spawning a second incremental job while one is already active, and
   a never-indexed guard returns `204` with no job. (`api/routes_documents.py`)
4. **`ExtractGraphStrategy.finalize` returns `SUCCEEDED` (not `PARTIALLY_FAILED`)
   on empty units**, so the shrink job reaches `merge_delta` instead of aborting.
   (`engine/strategies/extract_graph.py`)
5. **Frontend copy + return signal**: `SettingsPage` stale delete copy now reflects
   the A3 auto-rebuild; `deleteDocument` returns `{ shrinkJobCreated, jobId? }` so
   the UI can surface the auto shrink job. (`web/src/...`)

## Regression results

### Backend

```
uv run pytest            → 252 passed, 1 warning in 12.65s
uv run ruff check .      → All checks passed!
```

- 252 passed, 0 failed.
- The single warning is a pre-existing `StarletteDeprecationWarning` from
  `fastapi.testclient` re: `httpx` vs `httpx2` — unrelated to A3.
- Ruff: clean.

### Frontend

```
cd web && npm test       → Test Files  19 passed (19) | Tests  73 passed (73)
cd web && npm run build  → tsc -b && vite build → built in 1.64s (clean)
```

- 19 test files, 73 tests passed.
- Build: clean, 1114 modules transformed; warnings emitted during tests are
  pre-existing React Router future-flag and `act(...)` notices, unrelated to A3.

## Contracts covered by the test suite

### `merge_delta` filter + prune + best-effort + empty
File: `tests/test_merge_delta.py`

- `test_merge_delta_combines_extractions_for_live_chunks` — live chunks' extractions
  are merged.
- `test_merge_delta_filters_and_prunes_orphan_extractions` — extractions whose chunk
  is no longer in the chunk table are filtered out and their `extractions/*.json`
  sidecars are pruned.
- `test_merge_delta_prune_failure_is_best_effort` — a prune failure (e.g. `OSError`)
  is logged and swallowed; the merge still completes. The shrink never fails because
  of an un-removable sidecar.
- `test_merge_delta_empty_when_no_live_chunks` — with no live chunks the merged
  extraction is empty (the delete-to-empty path).

### Delete auto-job / coalesce / guard (202 / 204)
File: `tests/test_api_documents.py`

- `test_delete_auto_creates_shrink_job_when_indexed` — DELETE on an indexed KB
  returns `202` and creates an incremental job.
- `test_delete_no_job_when_never_indexed` — DELETE on a never-indexed KB returns
  `204` with no job (never-indexed guard).
- `test_delete_coalesces_when_incremental_job_active` — DELETE while an incremental
  job is already running returns `204` and does **not** spawn a second job
  (coalesced).
- `test_delete_creates_job_when_only_full_job_active` — DELETE while only a full
  job is running returns `202` and **does** create the incremental shrink job.
- `test_delete_document_cascades_chunks` / `test_delete_document_wrong_kb_404` /
  `test_delete_missing_document_404` — existing cascade + 404 contracts still hold.

### Delete-to-empty yields an empty graph
File: `tests/test_incremental_pipeline.py`

- `test_delete_doc_shrinks_unique_entities_keeps_shared` — after deleting a document,
  entities unique to it disappear from `entities.parquet`; entities shared with a
  remaining document are kept (end-to-end shrink, not just a row delete).
- `test_delete_last_doc_yields_empty_graph` — deleting the last document yields an
  empty `entities.parquet` (column-correct, zero rows) via the new
  `write_text_units_parquet` empty-write path.

### Frontend copy + DeleteResult signal
File: `web/src/components/DocumentManager.test.tsx`

- Covers the updated delete copy and the `{ shrinkJobCreated, jobId? }` return
  contract from `deleteDocument`.

## Optional real-server+worker browser smoke (left for the user)

The unit/integration suites use `FakeGraphAdapter` / `FakeQueryEngine`, so a real
LLM run is the remaining confidence step. Recommended manual smoke:

1. Start the API server and worker against a real provider profile:
   ```bash
   uv run python -m kb_platform.server
   uv run python -m kb_platform.worker
   ```
2. Create a KB, upload two documents with overlapping entities, and run a full
   index. Confirm the KB overview 图谱规模 card shows non-zero counts.
3. Delete one document. Confirm:
   - the DELETE call returns `202` with a `jobId`,
   - an incremental job appears and progresses to `SUCCEEDED`,
   - after it finishes, `entities.parquet` shrinks (unique entities gone, shared
     kept) and the KB overview 图谱规模 counts drop accordingly.
4. Delete the remaining document. Confirm the incremental job runs and the graph
   ends empty (zero entities / relationships / communities).

## Conclusion

A3 is fully covered by the automated suite: backend 252 passed / ruff clean,
frontend 73 passed / build clean. The merge_delta filter+prune fix, the delete
auto-job with coalescing + never-indexed guard, the delete-to-empty path, and the
frontend copy + `DeleteResult` contract are all exercised. The only remaining step
is the optional real-LLM browser smoke described above.
