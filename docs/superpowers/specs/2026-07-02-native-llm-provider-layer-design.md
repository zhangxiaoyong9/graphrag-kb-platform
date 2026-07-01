# Native LLM Provider Layer (strip litellm, native SSE + circuit breaker + failover)

- **Status:** design (pending plan)
- **Branch:** `feat/neo4j-cypher-export` → spin off `feat/native-llm-layer`
- **Scope:** platform-wide. Replaces every `graphrag-llm` chat-completion / embedding
  construction with a self-owned `kb_platform/llm/` layer that talks to
  OpenAI-compatible providers directly (httpx), parses provider SSE natively,
  and runs health-probe-driven failover across an ordered list of provider
  profiles.

## 1. Motivation

Today the LLM "provider abstraction" is `graphrag-llm`'s `create_completion` /
`create_embedding`, which internally use `litellm`. The platform only wraps the
result (`CostCapturingCompletion` → `LoadBalancingCompletion`). This drives four
pain points the new layer addresses together:

- **Reliability.** `LoadBalancingCompletion` round-robins API keys but has **no
  failover**: one flaky key/endpoint (429 / 5xx / timeout) fails the indexing
  unit or the query. We want live, probe-driven failover so a single bad key or
  endpoint cannot sink a job or a query.
- **Streaming quality.** litellm sits between the provider's SSE and our SSE; the
  existing `_StreamFixWrapper` hack (graphrag-llm returns a coroutine instead of
  an async iterator for `stream=True`) is a symptom of that indirection. We want
  first-token latency and clean incremental deltas we fully control.
- **Cross-provider failover.** Failover must be able to cross providers (OpenAI →
  DeepSeek → Ollama), not just cross-key within one provider.
- **Dependency / control.** Drop litellm from every runtime LLM/embedding call;
  own the wire format. (litellm remains *importable* transitively — graphrag-llm
  imports it at module load and calls `nest_asyncio.apply()` — but no litellm
  completion/embedding call is made on any indexing or query hot path.)

**Non-goals.**

- Forking or vendoring graphrag / graphrag-llm. We use graphrag-llm's own
  extension point (§5) and keep it as a dependency.
- Multi-tenant routing, prompt routing, or token-budget schedulers.
- A new embedding *profile* model. Embeddings keep a single profile
  (`embedding_profile_id`); only chat completions get cross-profile failover.
- Changing the platform's own SSE wire format to the browser
  (`api/sse.py`: `meta`/`delta`/`done`/`error`). That stays.

## 2. Current state (what changes)

- `kb_platform/graph/graphrag_adapter.py::build_default_adapter` calls
  `graphrag_llm.completion.create_completion(model_config)` and wraps it with
  `CostCapturingCompletion`; multiple keys additionally wrap with
  `LoadBalancingCompletion`. The same `completion` is injected into graphrag's
  `GraphExtractor` / `SummarizeExtractor` / `CommunityReportsExtractor`.
- `build_chat_complete` (rewriter) builds its own `create_completion`.
- `kb_platform/query/graphrag_engine.py::_resolve_config` builds
  `completion_models` / `embedding_models` entries with `type="litellm"`;
  graphrag's `query/factory.py` then calls `create_completion` / `create_embedding`
  internally and wires `engine.model` + `context_builder.text_embedder`.
  `_StreamFixWrapper` patches `engine.model` for the basic method's streaming
  bug; the basic branch of `_run_graphrag_search` swaps it in.
- `CostCapturingCompletion` records `response.usage` into a per-unit contextvar
  (`use_recorder()`). This is the indexing cost seam.
- No circuit breaker, no health probe, no live failover exists anywhere.
  `kb_platform/retry.py` is unit-level retry (reset PENDING, rerun later).

**What gets deleted:** `LoadBalancingCompletion`, `_StreamFixWrapper`, and the
basic-method swap branch. `CostCapturingCompletion` is **kept** (still wraps any
completion object; the indexing cost-capture contextvar seam is unchanged).

## 3. The decisive finding: graphrag-llm's factory registry

graphrag-llm exposes a first-class, str-typed extension hook:

```python
# graphrag_llm/completion/completion_factory.py
def register_completion(
    completion_type: str,                       # str, NOT locked to LLMProviderType
    completion_initializer: Callable[..., "LLMCompletion"],
    scope: ServiceScope = "transient",
) -> None: ...

def create_completion(model_config, ...) -> "LLMCompletion":
    ...
    if strategy not in completion_factory:      # only matches when UNregistered
        match strategy:
            case LLMProviderType.LiteLLM: ...   # LiteLLMCompletion
            case LLMProviderType.MockLLM: ...   # MockLLMCompletion
            case _: raise ValueError(...)
    ...
    return completion_factory.create(strategy=strategy, init_args={**extra, "model_id": model_id, ...})
```

- `LLMProviderType` is a `StrEnum`, so any plain string (e.g. `"kb_native"`) is a
  valid `ModelConfig.type`.
- If we **pre-register** our type, the `match` is skipped and our initializer is
  used for *every* `create_completion(model_config)` call — ours **and** graphrag's
  query factory's (which imports and calls the same `create_completion`).
- The same pattern exists for embeddings (`register_embedding` + `create_embedding`
  in `graphrag_llm/embedding/embedding_factory.py`).
- Construction is cheap: `create_completion` only builds a tokenizer / optional
  rate-limiter / retry / metrics and instantiates the class. No network. The
  litellm network calls live exclusively in `LiteLLMCompletion.completion[_async]`
  — which we never reach once our type is registered.

**Consequence:** setting `ModelConfig.type = "kb_native"` everywhere we build
configs (indexing adapter, rewriter, query `_resolve_config`) and registering
`NativeCompletion` / `NativeEmbedding` for that type at startup is the entire
integration. No monkeypatch, no post-construction attribute swap, no discarded
litellm objects, no litellm network call on any hot path.

## 4. Architecture

### 4.1 Module layout — `kb_platform/llm/`

```
events.py          Normalized event dataclasses: TextDelta / ToolCallDelta / Usage / Done / Error
sse.py             Provider-side OpenAI-compatible SSE stream parser → events.
                   (Distinct from api/sse.py, which is our OWN wire format to the browser.)
request.py         Per-provider request normalization: URL shape, auth header, api-version.
client.py          NativeCompletion(LLMCompletion): single-profile httpx chat transport
                   (stream + non-stream + structured_output passthrough); owns one FailoverGateway.
embedding.py       NativeEmbedding: httpx POST /v1/embeddings, batched, retried per key.
circuit_breaker.py CircuitBreaker: closed/open/half-open; consecutive-failure threshold + open TTL.
health.py          HealthProbe: shared background loop, pings each profile on a cadence, feeds breakers.
gateway.py         FailoverGateway: ordered profiles + per-profile breakers; picks a healthy profile,
                   retries retriable errors on the next profile, records timing metrics.
metrics.py         TTFT / failover-detection / failover-recovery recorders + structured logging.
registry.py        Startup registration: register_completion/register_embedding("kb_native", …).
                   Reconstructs the gateway from ModelConfig.model_extra in NativeCompletion.__init__.
bootstrap.py       Called once from server.py + worker.py entrypoints to run registry + start HealthProbe.
```

### 4.2 Normalized event schema (`events.py`)

```python
@dataclass
class TextDelta:        text: str
@dataclass
class ToolCallDelta:    index: int; id: str | None; name: str | None; args_chunk: str
@dataclass
class Usage:            prompt_tokens: int; completion_tokens: int
@dataclass
class Done:             pass
@dataclass
class Error:            message: str; retriable: bool
StreamEvent = TextDelta | ToolCallDelta | Usage | Done | Error
```

- `TextDelta` / `Usage` / `Done` / `Error` are exercised by graphrag's map-reduce
  and by our query SSE path today.
- `ToolCallDelta` is **reserved** for future agent / MCP tool-use. graphrag's query
  engines do not use tool calls, so nothing consumes it yet; it is in the schema so
  the SSE parser and the event contract do not need a breaking change later.

### 4.3 Provider SSE parser (`sse.py`)

- Reads an httpx byte stream line-by-line; splits `data: <json>` frames; handles
  the terminal `data: [DONE]`.
- For each frame, reads `choices[0].delta.content` → `TextDelta`,
  `choices[0].delta.tool_calls[…]` → `ToolCallDelta`, and the final frame's
  `usage` → `Usage` (OpenAI-compatible providers put usage on the last chunk when
  `stream_options.include_usage`).
- On a non-200 status or a frame that fails to parse → `Error(retriable=…)`.
  Network/timeout → `Error(retriable=True)`. 4xx auth → `Error(retriable=False)`
  at the HTTP layer, but the gateway still treats a profile-level auth failure as
  failover-eligible (§4.6).

This is deliberately separate from `api/sse.py`, which serializes *our* SSE to the
browser. The two never share code: one parses the provider wire format, the other
emits ours.

### 4.4 Request normalization (`request.py`)

One OpenAI-compatible request body; provider differences are URL + headers only:

| provider | URL | auth |
|---|---|---|
| `openai` / `deepseek` | `{api_base or default}/chat/completions` | `Authorization: Bearer {key}` |
| `ollama` | `{api_base or http://localhost:11434}/v1/chat/completions` | none (placeholder key ignored) |
| `azure` | `{api_base}/openai/deployments/{model}/chat/completions?api-version={api_version}` | `api-key: {key}` |

`structured_output`: when the caller passes `response_format` (json_schema),
include it verbatim in the request body. (`report_community_plain` passes
`response_format=None`; DeepSeek keeps using that plain path.) Embeddings use the
same URL/header table against `/embeddings` (Azure: `…/openai/deployments/{model}/embeddings?api-version=…`).

### 4.5 `NativeCompletion` / `NativeEmbedding` contract

`NativeCompletion` subclasses `graphrag_llm.completion.completion.LLMCompletion`
(duck-typed is acceptable; subclassing is preferred so `isinstance` and the
registry's type checks hold). It must satisfy what graphrag + our wrappers read:

- `.model_id` (from init_args)
- `.tokenizer` — constructed once via graphrag's `create_tokenizer` /
  `get_tokenizer(encoding_model)` so graphrag's `tokenizer = chat_model.tokenizer`
  works unchanged.
- `async def completion_async(self, messages, *, stream, response_format=None, **kwargs)`
  - `stream=False` → drives the gateway to completion, aggregates `TextDelta` into
    `.content`, reads `Usage` into `.usage` (prompt_tokens / completion_tokens),
    returns a graphrag-llm-shaped response (`.content`, `.usage`, `.output`).
  - `stream=True` → returns an **async iterator** of provider chunks directly
    (proper `__aiter__`/`__anext__`; this is what retires `_StreamFixWrapper`).
- `def completion(self, …)` — sync wrapper (used by `extract_chunk_sync` test helper).
- Accepts the registry's `init_args`: `__init__(self, *, model_id, **extra)` where
  `extra` carries the resolved profile bundle (see §4.7).

`NativeEmbedding` exposes `embedding(input=list[str]) -> response(.embeddings)`,
batched at `_EMBED_BATCH_SIZE` (carried over from the current adapter), with
per-key round-robin and retriable-error retry **within the single profile** (no
cross-profile failover for embeddings, per non-goals).

### 4.6 Circuit breaker + health probe

`CircuitBreaker(failure_threshold=N, open_seconds=T)` per profile:

- **closed** → requests pass; each retriable failure increments the count; on N in
  a row → **open**.
- **open** → requests skip this profile; after `open_seconds` → **half-open**.
- **half-open** → a single probe/request is allowed; success → **closed** (reset),
  failure → **open** (restart TTL).

`HealthProbe` is one shared background `asyncio` task per process (started in
`bootstrap.py`; the worker and the server each run one). Every `probe_interval`
(default 60s) it issues a tiny `max_tokens=1` completion against each profile:
- success in half-open → close;
- failure → drive the breaker toward open (counts as one failure).

Probe cost is bounded: ~one minimal completion per profile per minute.

### 4.7 Failover gateway (`gateway.py`)

`FailoverGateway` holds the ordered profile list (`[primary] + fallbacks`) and one
`CircuitBreaker` per profile. It is constructed inside `NativeCompletion.__init__`
from the profile bundle carried in `ModelConfig.model_extra`:

```python
# what gets packed into ModelConfig(extra=...) at config-build time:
{
  "kb_profiles": [ {provider, model, api_base, api_version, keys[...], ssl_verify, structured_output}, … ],
  "failover": {"failure_threshold": 5, "open_seconds": 30},
}
```

`gateway.complete(messages, stream, response_format)`:

1. Candidate order = ordered profiles whose breaker is `closed` or `half-open`.
2. For each candidate: attempt the call (round-robin keys within the profile).
   - success → record TTFT / tokens, reset that breaker, return events/response.
   - `Error(retriable=True)` or HTTP 5xx/429/timeout/parse-failure → record a
     failure (trip breaker at threshold), advance to the next candidate.
   - HTTP 401/403 (bad credential for this profile) → record a failure **and**
     advance; a different profile may carry valid creds, so this is failover-eligible
     even though the error itself is non-retriable for *that* profile.
3. All candidates exhausted (all open, or all failed) → raise. The caller handles
   it as today: indexing → unit FAILED (retryable later via `retry.py`); query →
   `StreamDone(error=…)` / `QueryResult(error=…)` (never a 500).

Per-key round-robin within a profile is preserved (it was `LoadBalancingCompletion`'s
job; that class is deleted and its responsibility moves here).

### 4.8 Metrics (`metrics.py`)

- **TTFT** — streaming calls: time from HTTP request sent to first `TextDelta`.
- **Failover detection** — time from first error to the decision to advance.
- **Failover recovery** — time from advance-decision to a successful response.

Emitted as structured log lines and accumulated in an in-memory store exposed at a
new `GET /llm/health` route (per-profile breaker state, last probe result, last
failure, recent TTFT/failover timings). The existing `/health` route is untouched.

### 4.9 Data model change (option a — KB holds an ordered profile list)

- Add `KnowledgeBase.llm_fallback_profile_ids: JSON` (ordered list of ints, nullable).
  Failover order = `[llm_profile_id] + llm_fallback_profile_ids`.
- `llm_profile_id` (primary) is **unchanged** → existing KBs have empty fallback
  list and behave exactly as today (single profile, but now through the native
  client with within-profile key round-robin + breaker).
- `embedding_profile_id` stays singular (no embedding failover).
- One alembic migration (`add_kb_llm_fallback_profiles`).
- `assemble_kb_settings` packs the ordered profile bundle into `llm.kb_profiles`
  so it flows through `ModelConfig.type="kb_native"` → `model_extra` →
  `NativeCompletion.__init__`.
- KB form UI gains an ordered multi-select for fallback LLM profiles.

## 5. Integration points

1. `kb_platform/llm/bootstrap.py` — called from `server.py` and `worker.py`
   entrypoints (before any adapter/engine is built): runs `register_completion`
   + `register_embedding` for `"kb_native"` and starts the shared `HealthProbe`.
2. Every place that builds a `ModelConfig` sets `type="kb_native"`:
   - `build_default_adapter` / `build_adapter_from_settings` (indexing)
   - `build_chat_complete` (rewriter)
   - `_resolve_config` → `completion_models` / `embedding_models` entries (query)
   Each packs the resolved `kb_profiles` bundle into `model_extra`.
3. **Delete** `LoadBalancingCompletion` (its job moves into `FailoverGateway`).
4. **Delete** `_StreamFixWrapper` and the basic-method swap in
   `_run_graphrag_search`; `NativeCompletion.completion_async(stream=True)` returns
   a proper async iterator so both workarounds are obsolete.
5. **Keep** `CostCapturingCompletion` wrapping `NativeCompletion` in
   `build_default_adapter` — it records `.usage` into the per-unit contextvar
   regardless of the underlying completion class.

## 6. Alternatives considered

- **Monkeypatch `create_completion`.** Rejected: graphrag's `query/factory.py`
  binds the name at import (`from graphrag_llm.completion import create_completion`),
  so patching the `graphrag_llm` module attribute does not intercept calls already
  bound into graphrag's own namespaces. Would require patching each importing
  module — fragile across graphrag versions. The factory registry (§3) is the
  supported hook.
- **Post-construction swap of `engine.model` / `context_builder.text_embedder`.**
  Rejected as primary strategy: graphrag's factory would still construct (then
  discard) a litellm object per engine, and we would have to enumerate and swap
  every embedder-bearing attribute per engine type; missing one silently leaves a
  litellm call on the hot path. The registry approach makes the swap automatic and
  side-effect-free. (The existing `_StreamFixWrapper` swap is removed, not extended.)

## 7. Phased delivery

The spec describes the full target; the implementation plan (writing-plans, next)
delivers it in behavior-preserving phases:

- **P1 — Native client, zero litellm calls, behavior parity (no failover yet).**
  `events` / `sse` / `request` / `NativeCompletion` / `NativeEmbedding` /
  `registry` / `bootstrap`; single-profile gateway (no breaker, no probe, but
  **with within-profile key round-robin** so multi-key KBs keep working after
  `LoadBalancingCompletion` is deleted); set `type="kb_native"` everywhere;
  delete `LoadBalancingCompletion` +
  `_StreamFixWrapper`; **guard test** asserting the indexing + query hot paths
  use `NativeCompletion`/`NativeEmbedding` and never instantiate
  `LiteLLMCompletion`.
- **P2 — Resilience.** `circuit_breaker` + `FailoverGateway` cross-profile
  failover; `llm_fallback_profile_ids` migration + `assemble_kb_settings` packing;
  KB form UI for ordered fallback selection.
- **P3 — Proactive health.** `HealthProbe` background loop feeding breakers.
- **P4 — Observability.** `metrics` (TTFT / failover detection+recovery) +
  `GET /llm/health`.

Each phase leaves the platform shippable: P1 alone already delivers the
"zero litellm + clean streaming" outcome; P2 adds reliability; P3/P4 add
proactive management and visibility.

## 8. Testing

- **Unit (no network).** SSE parser fed by canned provider byte streams
  (OpenAI/DeepSeek/Ollama/Azure shapes, `[DONE]`, mid-stream error, usage-on-last).
  Request normalization table (URL/headers per provider, structured_output body).
  CircuitBreaker state transitions. FailoverGateway with a fake transport that
  injects per-profile errors → assert advance order, breaker trips, final raise
  when all open.
- **Fake transport.** A `FakeTransport` (analogous to `FakeGraphAdapter`) returns
  deterministic `TextDelta`/`Usage` sequences and scriptable failures, so the
  gateway + NativeCompletion are tested without real HTTP.
- **Guard test (P1).** Build a real adapter from `kb_native` settings and a real
  query engine; assert `isinstance(completion, NativeCompletion)`,
  `isinstance(engine.model, NativeCompletion)`, and that
  `LiteLLMCompletion.__init__` is never invoked across an indexing + query
  exercise. This is the regression net that proves litellm stays off the hot path.
- **Integration (real key, opt-in).** A profile with a real key exercised via the
  API, same as today's real-LLM integration tests.

## 9. Risks / open questions

- **`ModelConfig.model_extra` plumbing.** Packing `kb_profiles` (with decrypted
  keys) through `ModelConfig(extra=...)` → `completion_factory.create(init_args)`
  → `NativeCompletion.__init__` must be verified against the exact `init_args`
  spread in `create_completion` (P1 first task). If `ModelConfig` does not accept
  arbitrary extras, fall back to a single side-channel: a module-level
  `{model_id: resolved_bundle}` registry keyed on the synthesized `model_id`.
- **graphrag-llm singleton scope.** `LiteLLMCompletion` registers as
  `"singleton"`; we register `"kb_native"` as **`"transient"`** so each
  `create_completion` call rebuilds a gateway from the (per-KB) bundle — singleton
  would cache one gateway across KBs.
- **Tokenizer fidelity.** `NativeCompletion.tokenizer` must match graphrag's
  expectations (used for context windowing in map-reduce). Reuse graphrag's
  `get_tokenizer`/`create_tokenizer`; do not invent one.
- **Probe loop lifecycle.** One shared task per process; must be started after the
  asyncio loop is running (server/worker entrypoints) and cancelled on
  SIGTERM/SIGINT alongside the existing handlers.
- **Azure `api_version` requirement.** Azure requests fail without `api_version`;
  the request normalizer must treat a missing `api_version` for `provider=azure`
  as a config error surfaced at first call (not a silent litellm default).

## 10. Out of scope

- Embedding cross-profile failover (single embedding profile retained).
- Token-budget / concurrency schedulers, prompt routing, model fallback by query
  type.
- Any change to the browser-facing SSE contract (`api/sse.py`) or the
  `QueryEngine` / `ConversationService` event shapes (`StreamDelta`/`StreamDone`).
- Forking graphrag or graphrag-llm.
