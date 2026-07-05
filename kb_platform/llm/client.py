"""NativeCompletion: graphrag-llm LLMCompletion backed by our FailoverGateway.

Adapts the gateway's normalized events to the OpenAI shapes graphrag consumes:
- non-stream -> LLMCompletionResponse (choices[0].message.content + usage)
- stream     -> openai ChatCompletionChunk (choices[0].delta.content)

stream return is an _AwaitableAsyncIterator: BOTH ``async for chunk in X``
(graphrag basic_search, no await) AND ``async for chunk in await X`` (local /
global / drift) work. This is what retires _StreamFixWrapper for all engines."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.completion_usage import CompletionUsage
from pydantic import BaseModel

try:
    from graphrag_llm.completion.completion import LLMCompletion
    from graphrag_llm.types.types import LLMCompletionChunk, LLMCompletionResponse
except Exception:  # pragma: no cover - graphrag-llm always present in prod
    class LLMCompletion:  # type: ignore[no-redef]
        pass

    LLMCompletionResponse = ChatCompletion       # type: ignore[assignment,misc]
    LLMCompletionChunk = ChatCompletionChunk     # type: ignore[assignment,misc]

from kb_platform.llm.events import Done, Error, TextDelta, Usage
from kb_platform.llm.gateway import ChatRequest, FailoverGateway, GatewayResult
from kb_platform.llm.request import ProviderConfig


class _AwaitableAsyncIterator:
    """An async iterator that is also awaitable (await returns self, no-op).

    Lets callers use EITHER ``async for chunk in X`` (basic_search) OR
    ``resp = await X; async for chunk in resp`` (local/global/drift)."""

    def __init__(self, gen: AsyncIterator[Any]) -> None:
        self._gen = gen

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._gen

    async def __anext__(self) -> Any:
        return await self._gen.__anext__()

    def __await__(self) -> Iterator[Any]:
        # no-op generator: ``await aai`` evaluates to self immediately.
        # ``if False: yield`` makes this a generator function so the ``return``
        # value is delivered via StopIteration.value (the await protocol).
        if False:  # pragma: no cover
            yield  # pragma: no cover
        return self


def _profile_configs(model_config: Any) -> list[ProviderConfig]:
    """Read the ``kb_profiles`` bundle packed into ModelConfig extras."""
    extra = getattr(model_config, "model_extra", None) or {}
    raw = extra.get("kb_profiles") or []
    out: list[ProviderConfig] = []
    for p in raw:
        keys = p.get("keys") or []
        out.append(
            ProviderConfig(
                provider=p["provider"],
                model=p["model"],
                api_base=p.get("api_base"),
                api_version=p.get("api_version"),
                key=keys[0] if keys else None,
                ssl_verify=p.get("ssl_verify", True),
            )
        )
    return out


class NativeCompletion(LLMCompletion):
    """graphrag-llm completion backed by our native gateway.

    Registered as the ``kb_native`` completion type (registry.py). Built per
    create_completion call (scope='transient') so each KB gets its own gateway
    from the ``kb_profiles`` bundle in ModelConfig extras."""

    def __init__(
        self,
        *,
        model_id: str,
        model_config: Any,
        tokenizer: Any = None,
        metrics_store: Any = None,
        metrics_processor: Any = None,
        rate_limiter: Any = None,
        retrier: Any = None,
        cache: Any = None,
        cache_key_creator: Any = None,
        gateway: FailoverGateway | None = None,
        **kwargs: Any,
    ) -> None:
        # graphrag-llm's LLMCompletion declares ``tokenizer`` / ``metrics_store``
        # as abstract properties; store the factory-injected values and expose
        # them via the property accessors below so create_completion() succeeds.
        self.model_id = model_id
        self._tokenizer = tokenizer
        self._metrics_store = metrics_store
        self._metrics_processor = metrics_processor
        self._cache = cache
        self._cache_key_creator = cache_key_creator
        self._model_config = model_config
        profiles = _profile_configs(model_config)
        if gateway is not None:
            self._gateway = gateway
        else:
            # Profiles in P1 share one KB -> one ssl_verify; use the first
            # profile's setting. Self-signed endpoints set ssl_verify=False on
            # every profile. The client is process-wide/shared (see http_client).
            verify = profiles[0].ssl_verify if profiles else True
            from kb_platform.llm.http_client import get_client
            client = get_client(verify)
            from kb_platform.llm.breaker_registry import breaker_for
            failure_threshold = kwargs.get("failure_threshold", 5)
            open_seconds = kwargs.get("open_seconds", 30.0)
            # Shared (process-wide) breakers keyed by endpoint identity, so the
            # background HealthProbe can drive the SAME breaker instances this
            # gateway reads. Each lookup also refreshes the stored config so the
            # probe always has fresh keys.
            breakers = {
                i: breaker_for(
                    profiles[i],
                    failure_threshold=failure_threshold,
                    open_seconds=open_seconds,
                )
                for i in range(len(profiles))
            }
            self._gateway = FailoverGateway(
                profiles=profiles, client=client, breakers=breakers,
                failure_threshold=failure_threshold, open_seconds=open_seconds,
            )

    # --- abstract property impls (LLMCompletion contract) ---
    @property
    def tokenizer(self) -> Any:  # noqa: D401 - LLMCompletion contract
        return self._tokenizer

    @property
    def metrics_store(self) -> Any:
        return self._metrics_store

    @property
    def metrics_processor(self) -> Any:
        return self._metrics_processor

    @property
    def cache(self) -> Any:
        return self._cache

    @property
    def cache_key_creator(self) -> Any:
        return self._cache_key_creator

    # test-only constructor bypass (avoids graphrag-llm factory in unit tests)
    def _init_for_test(self, **kw) -> None:  # noqa: D401 - test seam
        # Remap public attribute names to the underscore-prefixed backing fields
        # so the @property accessors above return them (data descriptors shadow
        # any same-named instance-dict entries).
        if "gateway" in kw:
            kw["_gateway"] = kw.pop("gateway")
        for pub, priv in {
            "tokenizer": "_tokenizer",
            "metrics_store": "_metrics_store",
            "metrics_processor": "_metrics_processor",
            "cache": "_cache",
            "cache_key_creator": "_cache_key_creator",
        }.items():
            if pub in kw:
                kw[priv] = kw.pop(pub)
        self.__dict__.update(kw)

    # --- async ---
    def completion_async(self, /, **kwargs: Any):  # type: ignore[override]
        messages = kwargs.get("messages")
        stream = bool(kwargs.get("stream"))
        response_format = kwargs.get("response_format")
        params = {k: v for k, v in kwargs.items()
                  if k not in {"messages", "stream", "response_format"}}
        req = ChatRequest(messages=_coerce_messages(messages), stream=stream,
                          response_format=response_format, params=params)
        if stream:
            return _AwaitableAsyncIterator(self._stream_chunks(req))
        return self._non_stream(req)

    async def _non_stream(self, req: ChatRequest) -> ChatCompletion:
        res: GatewayResult = await self._gateway.collect(req)
        # The gateway returns ``GatewayResult(error=...)`` (no exception) when every
        # profile is exhausted (e.g. all returned 5xx/429/timeout). Surface it as an
        # exception — otherwise graphrag's extractor sees ``response.content == ""``
        # and silently marks the unit SUCCEEDED with zero entities (the
        # 504 → empty-extraction bug; on_error/_raise_on_error never fires because
        # no exception propagates from the LLM call).
        if res.error:
            raise RuntimeError(f"LLM gateway: {res.error}")
        # LLMCompletionResponse subclasses ChatCompletion and adds the .content /
        # .formatted_response graphrag reads (e.g. drift primer.model_response.content
        # and CommunityReportsExtractor's parsed CommunityReportResponse).
        response = LLMCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            object="chat.completion",
            created=int(time.time()),
            model=self.model_id,
            choices=[Choice(index=0, message=ChatCompletionMessage(role="assistant", content=res.content),
                            finish_reason="stop")],
            usage=CompletionUsage(prompt_tokens=res.usage[0], completion_tokens=res.usage[1],
                                  total_tokens=sum(res.usage)),
        )
        # When graphrag requested a Pydantic model class as response_format, parse
        # the JSON content into that model so callers can read .formatted_response.
        # (graphrag-llm's LiteLLMCompletion does this via structure_completion_response.)
        # The streaming path does NOT support response_format (graphrag-llm rejects it).
        rf = req.response_format
        if isinstance(rf, type) and issubclass(rf, BaseModel):
            from graphrag_llm.utils import structure_completion_response

            try:
                response.formatted_response = structure_completion_response(res.content, rf)
            except Exception:  # noqa: BLE001 - graphrag tolerates None
                response.formatted_response = None
        return response

    async def _stream_chunks(self, req: ChatRequest) -> AsyncIterator[ChatCompletionChunk]:
        async for ev in self._gateway.astream(req):
            if isinstance(ev, TextDelta):
                yield _chunk(self.model_id, content=ev.text)
            elif isinstance(ev, Usage):
                yield _chunk(self.model_id, usage=ev)
            elif isinstance(ev, Done):
                # terminal marker from gateway — stop. We do NOT emit a synthetic
                # finish chunk here; graphrag's engines read choices[0].delta.content
                # and stop on their own, and an extra empty delta would change the
                # joined text by exactly one "". The stream just ends.
                return
            elif isinstance(ev, Error):
                # Terminal "all profiles exhausted" event from the gateway. Raise
                # so the consumer sees the failure — emitting a synthetic "stop"
                # chunk here would hand query engines an empty answer with no signal
                # that every upstream profile failed (silent degrade).
                raise RuntimeError(f"LLM gateway stream: {ev.message}")
        # stream ended without an explicit Done/Error event -> emit a terminal chunk
        yield _chunk(self.model_id, finish_reason="stop")

    # --- sync (used by extract_chunk_sync test helper) ---
    def completion(self, /, **kwargs: Any):  # type: ignore[override]
        import asyncio
        return asyncio.run(self.completion_async(**kwargs))


def _chunk(model_id: str, *, content: str | None = None, usage: Usage | None = None,
           finish_reason: str | None = None) -> ChatCompletionChunk:
    # LLMCompletionChunk subclasses ChatCompletionChunk; graphrag's basic_search /
    # local / global / drift all read choices[0].delta.content, which both expose.
    delta_kwargs: dict[str, Any] = {}
    if content is not None:
        delta_kwargs["content"] = content
    return LLMCompletionChunk(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model_id,
        choices=[ChunkChoice(index=0, delta=ChoiceDelta(**delta_kwargs), finish_reason=finish_reason)],
        usage=CompletionUsage(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                              total_tokens=usage.prompt_tokens + usage.completion_tokens) if usage else None,
    )


def _coerce_messages(messages: Any) -> list[dict[str, Any]]:
    """graphrag passes messages as str | list[dict]. Normalize to list[dict]."""
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    return list(messages or [])
