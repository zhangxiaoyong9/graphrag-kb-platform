"""NativeEmbedding: httpx POST /v1/embeddings, batched, single profile.

graphrag resolves embedding models via create_embedding; we register this class
as the ``kb_native`` embedding type. No cross-profile failover for embeddings
(single embedding_profile_id retained); within-profile keys round-robin."""

from __future__ import annotations

import itertools
from typing import Any

import httpx
from openai.types.create_embedding_response import Usage
from openai.types.embedding import Embedding
from graphrag_llm.types import LLMEmbeddingResponse

from kb_platform.llm.request import ProviderConfig, build_embed_request

_EMBED_BATCH_SIZE = 64


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
            # (self-signed endpoints set ssl_verify=False).
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                verify=self._profile.ssl_verify,
            )

    def embedding(self, *, input: list[str], **_kwargs: Any) -> LLMEmbeddingResponse:
        import asyncio
        return asyncio.run(self._embedding_async(input))

    async def _embedding_async(self, inputs: list[str]) -> LLMEmbeddingResponse:
        from dataclasses import replace

        all_vecs: list[list[float]] = []
        total = 0
        for start in range(0, len(inputs), _EMBED_BATCH_SIZE):
            batch = inputs[start : start + _EMBED_BATCH_SIZE]
            cfg = replace(self._profile, key=next(self._cycle))
            url, headers, body = build_embed_request(cfg, inputs=batch)
            resp = await self._client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                raise RuntimeError(f"embedding HTTP {resp.status_code}: {resp.text[:200]}")
            obj = resp.json()
            for item in obj.get("data", []):
                all_vecs.append(item["embedding"])
            u = obj.get("usage") or {}
            total += int(u.get("total_tokens", 0) or 0)
        return LLMEmbeddingResponse(
            object="list",
            model=self._profile.model,
            data=[Embedding(object="embedding", index=i, embedding=v) for i, v in enumerate(all_vecs)],
            usage=Usage(prompt_tokens=total, total_tokens=total),
        )
