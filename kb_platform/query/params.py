"""Three-layer query-param resolution: hardcoded baseline <- KB settings <- per-query.

No graphrag import here — this is pure layering. The engine applies the
resolved QueryParams (community_level/response_type are read directly;
top_k/temperature are injected into the resolved GraphRagConfig; system_prompt
overrides the method's primary answer-prompt slot).
"""
from kb_platform.query.engine import QueryParams

_FIELDS = ("community_level", "response_type", "top_k", "temperature", "system_prompt")


def resolve_query_params(kb_settings: dict | None, per_query: QueryParams | None) -> QueryParams:
    kb_settings = kb_settings or {}
    kbq = kb_settings.get("query_defaults") if isinstance(kb_settings, dict) else None
    kbq = kbq or {}

    def pick(name: str):
        per = getattr(per_query, name) if per_query is not None else None
        return per if per is not None else kbq.get(name)

    return QueryParams(**{name: pick(name) for name in _FIELDS})
