"""Self-owned LLM provider layer: native OpenAI-compatible transport,
SSE parsing, circuit breakers, and cross-profile failover.

Registered into graphrag-llm as the ``kb_native`` completion/embedding type
(see registry.py + bootstrap.py). No litellm network call is made on any
indexing or query hot path."""
