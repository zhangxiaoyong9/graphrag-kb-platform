"""Direct smoke of the kb_native LLM layer against a real provider profile.

Bypasses the graphrag index (so missing vectors/reports don't block) and
exercises NativeCompletion -> real provider HTTP -> SSE parse -> response,
plus the breaker registry + metrics. Usage: python scripts/smoke_native_llm.py [profile_id]"""
import asyncio
import json
import sys

import sqlite3

from kb_platform.db.crypto import decrypt_values, _fernet  # noqa: F401 (primes the cache)
from kb_platform.llm.registry import register_native
from kb_platform.llm.breaker_registry import snapshot as breaker_snapshot
from kb_platform.llm.metrics import METRICS


def load_profile(profile_id: int):
    con = sqlite3.connect("file:kb.db?mode=ro", uri=True)
    row = con.execute(
        "SELECT name, provider, model, api_base, api_version, api_keys_enc, ssl_verify FROM provider_profile WHERE id=?",
        (profile_id,),
    ).fetchone()
    con.close()
    if not row:
        raise SystemExit(f"profile {profile_id} not found")
    name, provider, model, api_base, api_version, keys_enc, ssl_verify = row
    keys = decrypt_values(keys_enc)
    if not keys:
        raise SystemExit(f"profile {name!r} has no decryptable keys (master key missing?)")
    print(f"[profile] id={profile_id} name={name!r} provider={provider} model={model} api_base={api_base} keys={len(keys)}")
    return {"provider": provider, "model": model, "api_base": api_base, "api_version": api_version,
            "keys": keys, "ssl_verify": bool(ssl_verify)}


async def main(profile_id: int):
    register_native()
    from graphrag_llm.config import ModelConfig
    from graphrag_llm.completion import create_completion
    from kb_platform.llm.client import NativeCompletion

    prof = load_profile(profile_id)
    mc = ModelConfig(type="kb_native", model_provider=prof["provider"], model=prof["model"],
                     api_base=prof["api_base"], api_version=prof["api_version"], api_key=prof["keys"][0],
                     kb_profiles=[prof])
    completion = create_completion(mc)
    print(f"[create_completion] -> {type(completion).__module__}.{type(completion).__name__}  (NativeCompletion? {isinstance(completion, NativeCompletion)})")

    msgs = [{"role": "user", "content": "Reply with exactly: PONG"}]

    print("\n=== non-stream ===")
    try:
        resp = await completion.completion_async(messages=msgs, stream=False)
        print("content:", repr(getattr(resp, "content", None)))
        u = getattr(resp, "usage", None)
        print("usage:", {"prompt": getattr(u, "prompt_tokens", None), "completion": getattr(u, "completion_tokens", None)})
    except Exception as e:
        print("non-stream ERROR:", type(e).__name__, str(e)[:300])

    print("\n=== stream ===")
    accumulated = ""
    try:
        it = await completion.completion_async(messages=msgs, stream=True)
        async for chunk in it:
            delta = chunk.choices[0].delta.content or "" if chunk.choices else ""
            if delta:
                accumulated += delta
        print("streamed text:", repr(accumulated))
    except Exception as e:
        print("stream ERROR:", type(e).__name__, str(e)[:300])

    print("\n=== /llm/health state ===")
    snap = breaker_snapshot()
    print("breakers:", [(k, cb.state) for k, (cb, _cfg) in snap.items()])
    print("metrics:", json.dumps(METRICS.snapshot()))

    # Clean shutdown of the shared httpx client pool (the server/worker do this
    # via bootstrap.close_clients on shutdown; mirrors that pattern here so the
    # smoke exits without httpcore teardown warnings).
    from kb_platform.llm.http_client import close_all

    await close_all()


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    asyncio.run(main(pid))
