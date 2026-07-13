"""NativeEmbedding: httpx POST /v1/embeddings, batched, single profile.

graphrag resolves embedding models via create_embedding; we register this class
as the ``kb_native`` embedding type. No cross-profile failover for embeddings
(single embedding_profile_id retained); within-profile keys round-robin."""

from __future__ import annotations

import itertools
import asyncio
import logging
import os
import time
from dataclasses import replace
from typing import Any

import httpx
from openai.types.create_embedding_response import Usage
from openai.types.embedding import Embedding
from graphrag_llm.types import LLMEmbeddingResponse

from kb_platform.llm.request import ProviderConfig, build_embed_request
from kb_platform.llm.observability import (
    input_stats,
    new_call_id,
    response_excerpt,
    safe_endpoint,
)
from kb_platform.logging_config import bind_log_context

_EMBED_BATCH_SIZE = 64
logger = logging.getLogger(__name__)


class NativeEmbedding:
    def __init__(self, *, model_id: str, model_config: Any, tokenizer: Any = None,
                 client: httpx.AsyncClient | None = None, keys: list[str] | None = None,
                 **_kwargs: Any) -> None:
        extra = getattr(model_config, "model_extra", None) or {}
        prof = (extra.get("kb_profiles") or [{}])[0]
        self._profile = ProviderConfig(
            provider=prof.get("provider", "openai"),
            model=prof.get("model", "text-embedding-3-small"),
            api_base=prof.get("api_base"),
            api_version=prof.get("api_version"),
            key=(prof.get("keys") or [None])[0],
            ssl_verify=prof.get("ssl_verify", True),
        )
        self._keys = keys if keys is not None else (prof.get("keys") or [])
        self._cycle = itertools.cycle(self._keys or [self._profile.key or ""])
        if client is not None:
            self._client = client
        else:
            # NativeEmbedding is single-profile; honor that profile's ssl_verify
            # (self-signed endpoints set ssl_verify=False). Shared client pool.
            from kb_platform.llm.http_client import get_client
            self._client = get_client(self._profile.ssl_verify)

    def embedding(self, *, input: list[str], **_kwargs: Any) -> LLMEmbeddingResponse:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.embed_many_async(input))
        raise RuntimeError(
            "NativeEmbedding.embedding() cannot run inside an active event loop; "
            "use await embed_many_async()"
        )

    async def embed_async(self, text: str) -> list[float]:
        """Embed a single text in the CALLER's event loop.

        Use this (not the sync ``embedding()``) from any async caller that shares
        the process-wide httpx client pool with other async code. ``embedding()``
        uses ``asyncio.run``, which spins up a throwaway loop; if the shared
        ``httpx.AsyncClient`` is first exercised there it binds to that loop, and
        any later async use (e.g. the Neo4j query engine's streaming synthesis)
        raises ``... bound to a different event loop``. Awaiting
        ``_embedding_async`` directly keeps everything on the caller's loop.
        """
        resp = await self.embed_many_async([text])
        return resp.embeddings[0]

    async def embed_many_async(self, inputs: list[str]) -> LLMEmbeddingResponse:
        """Embed a collection on the caller's loop with batch diagnostics and retries."""
        call_id = new_call_id("emb")
        all_vecs: list[list[float]] = []
        total = 0
        batches = (len(inputs) + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE
        chars, digest = input_stats(inputs)
        with bind_log_context(llm_call_id=call_id, operation="embedding"):
            logger.info(
                "embedding.start provider=%s model=%s items=%d batches=%d "
                "input_chars=%d input_hash=%s",
                self._profile.provider, self._profile.model, len(inputs), batches, chars, digest,
            )
            started = time.perf_counter()
            for batch_no, start in enumerate(range(0, len(inputs), _EMBED_BATCH_SIZE), 1):
                batch = inputs[start : start + _EMBED_BATCH_SIZE]
                vectors, tokens = await self._embed_batch(batch, batch_no, batches)
                all_vecs.extend(vectors)
                total += tokens
            dimensions = len(all_vecs[0]) if all_vecs else 0
            logger.info(
                "embedding.done provider=%s model=%s items=%d vectors=%d dimensions=%d "
                "tokens=%d duration_ms=%.0f",
                self._profile.provider, self._profile.model, len(inputs), len(all_vecs),
                dimensions, total, (time.perf_counter() - started) * 1000,
            )
        return LLMEmbeddingResponse(
            object="list",
            model=self._profile.model,
            data=[Embedding(object="embedding", index=i, embedding=v) for i, v in enumerate(all_vecs)],
            usage=Usage(prompt_tokens=total, total_tokens=total),
        )

    # Backward-compatible private alias used by existing tests.
    async def _embedding_async(self, inputs: list[str]) -> LLMEmbeddingResponse:
        return await self.embed_many_async(inputs)

    async def _embed_batch(
        self, batch: list[str], batch_no: int, batch_total: int
    ) -> tuple[list[list[float]], int]:
        raw_attempts = os.environ.get("KB_EMBED_MAX_ATTEMPTS", "3")
        try:
            max_attempts = max(1, int(raw_attempts))
        except ValueError:
            max_attempts = 3
            logger.warning(
                "embedding.invalid_config KB_EMBED_MAX_ATTEMPTS=%r; using 3",
                raw_attempts,
            )
        chars, digest = input_stats(batch)
        last_error = "unknown embedding failure"
        for attempt in range(1, max_attempts + 1):
            cfg = replace(self._profile, key=next(self._cycle))
            url, headers, body = build_embed_request(cfg, inputs=batch)
            t0 = time.perf_counter()
            logger.info(
                "embedding.batch_start provider=%s model=%s endpoint=%s batch=%d/%d "
                "attempt=%d/%d items=%d input_chars=%d input_hash=%s",
                cfg.provider, cfg.model, safe_endpoint(url), batch_no, batch_total,
                attempt, max_attempts, len(batch), chars, digest,
            )
            try:
                resp = await self._client.post(url, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {response_excerpt(exc)}"
                logger.warning(
                    "embedding.transport_error provider=%s model=%s batch=%d/%d "
                    "attempt=%d/%d duration_ms=%.0f error_type=%s error=%r",
                    cfg.provider, cfg.model, batch_no, batch_total, attempt, max_attempts,
                    (time.perf_counter() - t0) * 1000, type(exc).__name__, response_excerpt(exc),
                )
                if attempt < max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1) * 0.5, 2.0))
                    continue
                raise RuntimeError(f"embedding transport failure: {last_error}") from exc

            if resp.status_code >= 400:
                retriable = resp.status_code == 429 or resp.status_code >= 500
                excerpt = response_excerpt(resp.text)
                last_error = f"HTTP {resp.status_code}: {excerpt}"
                logger.log(
                    logging.WARNING if retriable else logging.ERROR,
                    "embedding.http_error provider=%s model=%s status=%d retriable=%s "
                    "batch=%d/%d attempt=%d/%d duration_ms=%.0f "
                    "upstream_request_id=%s response=%r",
                    cfg.provider, cfg.model, resp.status_code, retriable, batch_no,
                    batch_total, attempt, max_attempts,
                    (time.perf_counter() - t0) * 1000,
                    resp.headers.get("x-request-id") or resp.headers.get("request-id") or "-",
                    excerpt,
                )
                if retriable and attempt < max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1) * 0.5, 2.0))
                    continue
                raise RuntimeError(f"embedding {last_error}")

            try:
                obj = resp.json()
                data = obj.get("data") or []
                ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
                vectors = [item["embedding"] for item in ordered]
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "embedding.invalid_response provider=%s model=%s batch=%d/%d "
                    "duration_ms=%.0f content_type=%s response=%r",
                    cfg.provider, cfg.model, batch_no, batch_total,
                    (time.perf_counter() - t0) * 1000,
                    resp.headers.get("content-type", "-"), response_excerpt(resp.text),
                )
                raise RuntimeError(f"invalid embedding response: {exc}") from exc

            if len(vectors) != len(batch):
                logger.error(
                    "embedding.vector_count_mismatch provider=%s model=%s batch=%d/%d "
                    "expected=%d actual=%d",
                    cfg.provider, cfg.model, batch_no, batch_total, len(batch), len(vectors),
                )
                raise RuntimeError(
                    f"embedding vector count mismatch: expected {len(batch)}, got {len(vectors)}"
                )
            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) > 1:
                logger.error(
                    "embedding.dimension_mismatch provider=%s model=%s batch=%d/%d dimensions=%s",
                    cfg.provider, cfg.model, batch_no, batch_total, sorted(dimensions),
                )
                raise RuntimeError(f"embedding dimension mismatch: {sorted(dimensions)}")
            usage = obj.get("usage") or {}
            tokens = int(usage.get("total_tokens", 0) or 0)
            logger.info(
                "embedding.batch_success provider=%s model=%s batch=%d/%d items=%d "
                "dimensions=%d tokens=%d duration_ms=%.0f",
                cfg.provider, cfg.model, batch_no, batch_total, len(batch),
                next(iter(dimensions), 0), tokens, (time.perf_counter() - t0) * 1000,
            )
            return vectors, tokens
        raise RuntimeError(last_error)  # pragma: no cover
