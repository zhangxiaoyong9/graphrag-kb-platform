# Provider Profiles Design

Date: 2026-06-28

## Summary

Replace the per-KB "everything in `settings_yaml`" config model with **named
provider profiles** (global, reusable) for connection + key info, while KBs
keep only content/quality parameters. API keys move from env-only to
**frontend-entered, encrypted in the DB** (no env path). Existing KBs are
auto-migrated.

## Goals

1. Stop re-typing provider/model/api_base/key on every new KB: define a
   profile once, pick it from a dropdown at KB creation.
2. Manage API keys in the UI (encrypted at rest) instead of juggling env vars.
3. Keep KB-specific knobs (chunking, extraction, prompts, lengths) per-KB.
4. Leave the graphrag coupling (`build_adapter_from_settings`, ModelConfig
   construction) untouched — the split happens above it.

## Non-goals

- Per-KB provider override. A KB references exactly one LLM profile (+ optional
  embedding profile). To use a different provider, define another profile.
- Key rotation UI beyond add/remove in the list (no expire/audit log).
- Encryption with a KMS / external secret store. Master key is a local file.
- Restore the env-var key path. It is removed (see Security).

## Current context

- Every KB stores a full `settings_yaml`: `llm` (provider/model/api_base/
  `api_key_env`/api_version), `embedding` (same + enabled), chunking,
  extractGraph, summarize, communityReports (structured_output + maxLength),
  cluster, prompts, queryPrompts.
- Keys today are **never stored** — resolved at call time from
  `api_key_env` → `{PROVIDER}_API_KEY` env. Multi-key round-robin exists via
  `api_key_envs` + `LoadBalancingCompletion`.
- `PATCH /kbs/{id}` can update an existing KB's settings.
- No global config table; `/settings` page is read-only guidance.

## Architecture

### Data model

New table `provider_profile`:

| column | notes |
|---|---|
| id, name | name unique per `kind` |
| kind | `llm` \| `embedding` |
| provider, model, api_base, api_version | connection (api_version nullable, Azure) |
| api_keys_enc | TEXT nullable — JSON array of Fernet tokens (the only key source) |
| structured_output | bool (default true); used for `kind=llm`, ignored for embedding |
| created_at, updated_at | |

`knowledge_base` adds `llm_profile_id` (nullable FK) and
`embedding_profile_id` (nullable FK). `settings_json` is reduced to **content
params only**: chunking, extractGraph, summarize, communityReports.maxLength,
cluster, prompts, queryPrompts.

### Assembly seam `assemble_kb_settings(kb)`

A single helper used by the worker's `adapter_factory` and the query engine
(the graphrag side is unchanged — it still receives a full settings dict):

- `llm` ← `llm_profile`: provider, model, api_base, api_version,
  `api_keys` = decrypt(`api_keys_enc`).
- `embedding` ← `embedding_profile` if set; otherwise omitted (no vector
  methods for that KB).
- `community_reports` = `{structured_output: llm_profile.structured_output,
  maxLength: kb.settings.communityReports.maxLength}` — capability follows
  the model, length follows the KB.
- chunking / extractGraph / summarize / cluster / prompts / queryPrompts ←
  `kb.settings_json`.

If `llm_profile_id` is null or `api_keys_enc` is empty, assembly raises a
clear error (the KB cannot run without a key).

### Key encryption

- Symmetric encryption: `cryptography.fernet.Fernet`.
- Master key source: env `KB_SECRET_KEY` if set; otherwise **auto-generated**
  and persisted to `<dirname(db_path)>/.kb_secret_key` (chmod 600), loaded
  once at startup. (Operator action: none.)
- Write: `api_keys_enc = json.dumps([fernet.encrypt(k.encode()).decode() for k in keys])`.
- Read at call time: decrypt the list → handed to `LoadBalancingCompletion`
  for round-robin (same multi-key mechanism that exists today, now fed
  literals instead of env-resolved values).

### API

- `GET /provider-profiles?kind=llm|embedding` → list; each item has
  `api_keys_count` and **never** plaintext.
- `POST /provider-profiles` `{name, kind, provider, model, api_base,
  api_version, structured_output, api_keys: [...]}` → encrypts and stores.
- `PATCH /provider-profiles/{id}` — same fields; `api_keys` is write-only:
  omitted = unchanged, `[]` = clear, `[...]` = replace.
- `DELETE /provider-profiles/{id}` → **409 with the referencing-KB list** if
  any KB uses it (no orphaning).
- `POST /kbs` / `PATCH /kbs/{id}` take `llm_profile_id`, `embedding_profile_id`
  (+ nullable), `method`, `name`, and a content-only `settings_yaml`. Server
  validates the profile ids, assembles, persists.
- `GET /kbs/{id}` → `llm_profile {name, provider, model}`,
  `embedding_profile | null`, and content params. `_redact` no longer masks
  llm keys (none live in `settings_json`); it stays only for defense-in-depth
  on anything sensitive a user might paste into content prompts.

### UI

- New **Provider 配置** page under 系统管理 (`/provider-profiles`): list by
  kind, create/edit/delete. Form fields: name, kind, provider, model,
  api_base, api_version (Azure only), `structured_output` (llm only), and an
  **API Keys dynamic list** — default 1 password input, `+ 新增` to add a
  row, `✕` to remove; empty rows ignored on save. Edit mode shows
  `api_keys_count` and leaves the inputs blank (= unchanged) unless refilled.
- **KbForm rework**: remove the llm/embedding/provider/model/api_base/
  api_key sections; replace with a required **LLM 配置** `<select>` and an
  optional **Embedding 配置** `<select>` (with a "无" option). Keep the
  content sections (chunking, extractGraph, summarize,
  communityReports.maxLength, cluster, prompts, queryPrompts).
- KB detail: show selected profile names as read-only chips + content params.

### Migration (alembic, idempotent)

1. Create `provider_profile`; add nullable `llm_profile_id` /
   `embedding_profile_id` FKs on `knowledge_base`.
2. Backfill per KB: parse `settings_json`; from the `llm` block build a
   dedup key `(kind=llm, provider, model, api_base, api_version,
   structured_output)` → create/reuse an llm profile; set
   `KB.llm_profile_id`. If an `embedding` block is present and enabled, do
   the same for an embedding profile. `api_keys_enc` is left **empty** for
   migrated profiles (env-held keys are not pulled into the DB).
3. Strip `llm`, `embedding`, and `community_reports.structured_output` from
   `KB.settings_json`.
4. Idempotent: skip KBs that already have `llm_profile_id`.

Migrated/legacy KBs therefore need **keys re-entered** on the Provider page
before they can index or query.

## Security considerations

- Keys are now **always in the DB** (Fernet-encrypted). This retires the
  previous "keys never stored / env-only" posture — a deliberate trade of
  at-rest safety for UI convenience, accepted for this internal tool.
- At-rest protection is only as strong as the master-key file's protection.
  The file sits on the same disk as the DB (accepted); an operator can harden
  it by setting `KB_SECRET_KEY` env to a value stored off-disk.
- Plaintext keys never leave the write path: `GET` returns `api_keys_count`
  only; `_redact` keeps anything unexpected out of read responses.

## Testing plan

- **Backend**: profile CRUD (incl. 409 on referenced delete); Fernet
  encrypt/decrypt round-trip + master-key file bootstrap;
  `assemble_kb_settings` (profile+content→full, embedding omitted when null,
  structured_output follows llm_profile, raises on missing key); KB
  create/patch with profile ids; migration (seed a legacy KB → profiles
  created/deduped, KB repointed, settings stripped); a worker job run on
  assembled settings via `FakeGraphAdapter`.
- **Frontend**: Provider 配置 page (list/CRUD/dynamic-key-list, delete
  disabled when referenced); KbForm with profile dropdowns (submit blocked
  without an LLM profile; embedding optional).
- **E2E (downstream fix-up)**: the existing harness (`scripts/e2e_server.py`)
  and `createKbViaApi` post `{name, method}` today; after this change `POST
  /kbs` requires `llm_profile_id`, so the harness must seed a profile first
  and the Playwright create-KB spec must pass the profile id.

## Rollout order

1. Crypto helper (Fernet + master-key bootstrap) + tests.
2. `provider_profile` model + profile CRUD API + tests.
3. KB model changes + `assemble_kb_settings` seam + KB create/patch/detail.
4. Alembic migration (schema + idempotent backfill).
5. Provider 配置 page + KbForm rework + frontend tests.
6. E2E harness + create-KB spec fix-up; full green run.

## Open questions (resolved by this design)

- Profile flexibility model: **named profiles** (per-KB provider choice via
  separate profiles), not single-global or just-defaults.
- Profile boundary: connection + `structured_output` in profile; content
  params per-KB; embedding profile optional.
- Existing-KB migration: **auto-migrate** (alembic backfill + repoint +
  strip), one code path afterward.
- Master key: **auto-generated file** next to DB (option B); env override
  optional.
- Key input: **frontend-only**, encrypted list; env path removed.
