"""Per-provider request normalization for OpenAI-compatible chat + embedding.

One OpenAI-compatible request body; provider differences are URL + headers only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

_DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
_DEFAULT_OLLAMA_BASE = "http://localhost:11434/v1"


@dataclass
class ProviderConfig:
    provider: str           # openai | deepseek | ollama | azure
    model: str
    api_base: str | None
    api_version: str | None
    key: str | None
    ssl_verify: bool = True


def _ollama_base(cfg: ProviderConfig) -> str:
    base = (cfg.api_base or _DEFAULT_OLLAMA_BASE).rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _chat_url(cfg: ProviderConfig) -> str:
    if cfg.provider == "azure":
        base = (cfg.api_base or "").rstrip("/")
        return f"{base}/openai/deployments/{cfg.model}/chat/completions?api-version={cfg.api_version}"
    if cfg.provider == "ollama":
        return f"{_ollama_base(cfg)}/chat/completions"
    base = (cfg.api_base or _DEFAULT_OPENAI_BASE).rstrip("/")
    return f"{base}/chat/completions"


def _embed_url(cfg: ProviderConfig) -> str:
    if cfg.provider == "azure":
        base = (cfg.api_base or "").rstrip("/")
        return f"{base}/openai/deployments/{cfg.model}/embeddings?api-version={cfg.api_version}"
    if cfg.provider == "ollama":
        return f"{_ollama_base(cfg)}/embeddings"
    base = (cfg.api_base or _DEFAULT_OPENAI_BASE).rstrip("/")
    return f"{base}/embeddings"


def _auth_headers(cfg: ProviderConfig) -> dict[str, str]:
    if cfg.provider == "azure":
        return {"api-key": cfg.key or ""}
    if cfg.provider == "ollama":
        return {}
    return {"Authorization": f"Bearer {cfg.key}"}


def _normalize_response_format(rf: Any) -> Any:
    """Coerce a ``response_format`` argument into OpenAI wire format.

    graphrag passes a Pydantic model CLASS (e.g. ``CommunityReportResponse``);
    the raw class is not JSON-serializable so we expand it to a json_schema body
    here. A dict (already wire-format, e.g. a json_schema dict) passes through
    unchanged. ``None`` stays ``None``.
    """
    if rf is None:
        return None
    if isinstance(rf, dict):
        return rf
    if isinstance(rf, type) and issubclass(rf, BaseModel):
        return {
            "type": "json_schema",
            "json_schema": {"name": rf.__name__, "schema": rf.model_json_schema()},
        }
    return rf


def build_chat_request(
    cfg: ProviderConfig,
    *,
    messages: list[dict[str, Any]],
    stream: bool,
    response_format: Any,
    params: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers = {"Content-Type": "application/json", **_auth_headers(cfg)}
    body: dict[str, Any] = {"model": cfg.model, "messages": messages, "stream": stream, **params}
    if stream:
        body["stream_options"] = {"include_usage": True}
    if response_format is not None:
        body["response_format"] = _normalize_response_format(response_format)
    return _chat_url(cfg), headers, body


def build_embed_request(
    cfg: ProviderConfig, *, inputs: list[str]
) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers = {"Content-Type": "application/json", **_auth_headers(cfg)}
    return _embed_url(cfg), headers, {"model": cfg.model, "input": inputs}
