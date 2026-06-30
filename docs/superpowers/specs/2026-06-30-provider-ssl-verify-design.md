# Provider-profile SSL verification toggle

**Date:** 2026-06-30
**Status:** Approved (design)

## Problem

Embedding (and any) providers reachable only over HTTPS with a **self-signed
certificate** fail at the litellm call: httpx rejects the cert and the request
never completes (observed as the embed step stalling / non-200s). There is
currently no way to disable certificate verification per endpoint.

litellm accepts a per-call `ssl_verify` argument (`litellm.embedding(...,
ssl_verify=False)`, same for `completion`). graphrag-llm's `LiteLLMEmbedding`
and `LiteLLMCompletion` spread `**model_config.call_args` into that litellm
call, and `GraphRagConfig.embedding_models` / `llm_models` validate each entry
into graphrag-llm's `ModelConfig` (which has a `call_args: dict` field). So a
single injection point — `ModelConfig.call_args={"ssl_verify": ...}` — reaches
all three litellm call paths.

## Scope

Per-profile `ssl_verify` boolean (default **True**), on **both** LLM and
embedding provider profiles, covering all three litellm call paths:

1. Index-time LLM (extraction / summarize / reports) — `graphrag_adapter.build_adapter_from_settings`
2. Index-time embedding — `graphrag_adapter._build_embed_model_config` → `embed_items`
3. Query-time LLM + embedding — `kb_platform.query.graphrag_engine._resolve_config`

UI: dashboard provider-profile form gets a checkbox; API CRUD accepts the field.

**Out of scope:** MCP layer (pure HTTP proxy to the local API server, no
outbound TLS to model providers). Generic "extra litellm call_args" passthrough
(YAGNI — only `ssl_verify` is needed now).

## Design

### Data layer

- `ProviderProfile` (`kb_platform/db/models_profile.py`): new column
  `ssl_verify: Mapped[bool] = mapped_column(Boolean, default=True)`.
- Alembic migration `0008_provider_ssl_verify.py`: `add_column("provider_profile",
  sa.Column("ssl_verify", sa.Boolean(), nullable=False, server_default=sa.true()))`.
  `server_default` so existing rows get `True` without a backfill pass.

### Config resolution

`assemble_kb_settings` (`kb_platform/graph/graphrag_adapter.py`) reads the
profile flag into the settings dict consumed downstream:

- `assembled["llm"]["ssl_verify"] = lp.ssl_verify`
- `assembled["embedding"]["ssl_verify"] = ep.ssl_verify` (inside the existing
  `if kb.embedding_profile_id is not None:` block)

`build_adapter_from_settings` (LLM ModelConfig, ~line 448) and
`_build_embed_model_config` (~line 270) both pass it through:

```python
ModelConfig(..., call_args={"ssl_verify": llm.get("ssl_verify", True)})
```

Query path `kb_platform/query/graphrag_engine.py` `_resolve_config`: both the
`llm_models` entry (~line 560-580) and the `embedding_models` `entry`
(~line 600-610) gain:

```python
"call_args": {"ssl_verify": <llm|emb>.get("ssl_verify", True)}
```

These dicts are validated by `GraphRagConfig.model_validate` into graphrag-llm
`ModelConfig`, whose `call_args` flows into `litellm.{embedding,completion}`.

### API

`kb_platform/api/models.py`:

- `ProfileCreate`: `ssl_verify: bool = True`
- `ProfileUpdate`: `ssl_verify: bool | None = None`  (None = unchanged)
- `ProfileOut`: `ssl_verify: bool`

`kb_platform/api/routes_profiles.py` `_out()` serializes `ssl_verify`.
`create_profile` passes `**payload.model_dump()`; `update_profile` passes
`**payload.model_dump(exclude_unset=True)` — already generic.

`kb_platform/db/repository.py`:

- `create_profile` signature gains explicit `ssl_verify: bool = True` kwarg,
  threaded into the `ProviderProfile(...)` constructor (it is not a `**fields`
  method).
- `update_profile` is already a generic `setattr` loop guarded by
  `hasattr(p, k) and v is not None` — works unchanged for `ssl_verify` (False
  is not None → it sets; the column exists once migrated).

When a profile is created/updated with `ssl_verify=False`, emit
`logger.warning("provider profile '%s' has SSL verification disabled", name)`
so the insecure setting is visible in logs.

### Frontend

- `web/src/api/types.ts`: `ProviderProfile` type gains `ssl_verify: boolean`.
- `web/src/pages/ProviderProfilesPage.tsx`: form adds a checkbox
  `跳过 SSL 证书校验(自签证书)` (default unchecked), sent on create/update;
  shown read-only in the profile list/detail. Match surrounding Chinese copy.

### Tests

Backend (`pytest`):

- `assemble_kb_settings` propagates `ssl_verify=False` from each profile into
  `settings["llm"]["ssl_verify"]` / `settings["embedding"]["ssl_verify"]`.
- `build_adapter_from_settings` and `_build_embed_model_config` produce a
  `ModelConfig` whose `call_args["ssl_verify"]` matches the input (True and
  False cases; default True when absent).
- `graphrag_engine._resolve_config` embedding/llm entries contain
  `call_args.ssl_verify`.
- API: `POST`/`PATCH` persist `ssl_verify`; `ProfileOut` includes it.
- Migration: after `upgrade head`, the column exists and defaults True.

Frontend (`vitest`): form checkbox toggles `ssl_verify` in the submitted
payload.

## Security note

Default is secure (`ssl_verify=True`). Disabling verification is an explicit,
logged, per-profile opt-in for self-signed endpoints only. The checkbox label
calls out the risk.

## Risks / notes

- `repo.update_profile`'s `v is not None` guard means a future field that
  legitimately wants `None` would need special handling — not an issue for a
  boolean.
- graphrag-llm's `ModelConfig` is a pydantic model with `call_args` defaulting
  to an empty factory, so omitting it (profiles created before this feature,
  or unset) yields `{}` → litellm's default `ssl_verify=True` applies. Safe.
