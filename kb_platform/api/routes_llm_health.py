# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""GET /llm/health: per-endpoint circuit-breaker state + LLM call metrics.

Exposes the process-wide breaker registry (T16) and the gateway metrics
store (T17). Never leaks ``ProviderConfig.key`` — only provider/model/
api_base/state. Must be registered BEFORE the SPA catch-all in ``app.py``
so ``/llm/health`` is served as JSON, not ``index.html``.
"""

from fastapi import APIRouter

from kb_platform.llm.breaker_registry import snapshot as breaker_snapshot
from kb_platform.llm.metrics import METRICS

router = APIRouter()


@router.get("/llm/health")
async def llm_health() -> dict:
    profiles = []
    for _key, (cb, cfg) in breaker_snapshot().items():
        profiles.append(
            {
                "provider": cfg.provider,
                "model": cfg.model,
                "api_base": cfg.api_base,
                "state": cb.state,  # closed | open | half_open
            }
        )
    return {"profiles": profiles, "metrics": METRICS.snapshot()}
