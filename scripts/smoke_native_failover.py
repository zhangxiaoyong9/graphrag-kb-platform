"""Failover smoke: broken primary (dead endpoint) + real DeepSeek fallback.
Proves the gateway advances on a retriable error and the call succeeds via fallback."""
import asyncio
import json
import sqlite3

from kb_platform.db.crypto import decrypt_values, _fernet  # noqa: F401
from kb_platform.llm.registry import register_native
from kb_platform.llm.breaker_registry import snapshot as breaker_snapshot, _reset_for_test
from kb_platform.llm.metrics import METRICS


def deepseek_profile():
    con = sqlite3.connect("file:kb.db?mode=ro", uri=True)
    row = con.execute("SELECT provider, model, api_base, api_version, api_keys_enc, ssl_verify FROM provider_profile WHERE id=3").fetchone()
    con.close()
    provider, model, api_base, api_version, keys_enc, ssl_verify = row
    keys = decrypt_values(keys_enc)
    return {"provider": provider, "model": model, "api_base": api_base, "api_version": api_version,
            "keys": keys, "ssl_verify": bool(ssl_verify)}


async def main():
    register_native()
    _reset_for_test()  # start clean for this isolated smoke
    from graphrag_llm.config import ModelConfig
    from graphrag_llm.completion import create_completion

    fallback = deepseek_profile()
    broken_primary = {
        "provider": "openai", "model": "gpt-4o-mini",
        "api_base": "http://127.0.0.1:1",  # nothing listening -> ConnectError (retriable)
        "api_version": None, "keys": ["sk-dead"], "ssl_verify": True,
    }
    mc = ModelConfig(
        type="kb_native", model_provider=fallback["provider"], model=fallback["model"],
        api_base=fallback["api_base"], api_version=fallback["api_version"], api_key=fallback["keys"][0],
        kb_profiles=[broken_primary, fallback],  # [0]=broken primary, [1]=deepseek fallback
        failure_threshold=1, open_seconds=30.0,
    )
    completion = create_completion(mc)
    print("[gateway] 2 profiles: [0]=broken-primary (127.0.0.1:1), [1]=deepseek-fallback")

    print("\n=== stream query (expect failover -> 'PONG' from fallback) ===")
    accumulated = ""
    it = await completion.completion_async(
        messages=[{"role": "user", "content": "Reply with exactly: PONG"}], stream=True)
    async for chunk in it:
        d = chunk.choices[0].delta.content or "" if chunk.choices else ""
        accumulated += d
    print("streamed text:", repr(accumulated))

    print("\n=== breaker + metrics after failover ===")
    for k, (cb, cfg) in breaker_snapshot().items():
        print(f"  endpoint={k} state={cb.state}  (provider={cfg.provider})")
    print("  metrics:", json.dumps(METRICS.snapshot()))
    print("\nPASS" if accumulated.strip().upper().startswith("PONG") else "\nFAIL")

    # Clean shutdown of the shared httpx client pool (mirrors server/worker close_clients).
    from kb_platform.llm.http_client import close_all

    await close_all()


if __name__ == "__main__":
    asyncio.run(main())
