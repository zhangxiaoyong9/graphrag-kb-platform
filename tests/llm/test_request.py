from kb_platform.llm.request import ProviderConfig, build_chat_request, build_embed_request


def _cfg(provider, **kw):
    base = dict(provider=provider, model="m", api_base=None, api_version=None,
                key="k", ssl_verify=True)
    base.update(kw)
    return ProviderConfig(**base)


def test_openai_chat_request():
    url, headers, body = build_chat_request(
        _cfg("openai"), messages=[{"role": "user", "content": "hi"}],
        stream=True, response_format=None, params={"temperature": 0.1},
    )
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k"
    assert body["model"] == "m" and body["stream"] is True and body["temperature"] == 0.1
    assert "response_format" not in body


def test_deepseek_custom_api_base():
    url, headers, _ = build_chat_request(
        _cfg("deepseek", api_base="https://api.deepseek.com"),
        messages=[], stream=False, response_format=None, params={},
    )
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer k"


def test_ollama_no_auth_header():
    url, headers, _ = build_chat_request(
        _cfg("ollama", api_base="http://localhost:11434"),
        messages=[], stream=False, response_format=None, params={},
    )
    assert url == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in headers


def test_azure_deployment_url_and_apikey_header():
    url, headers, _ = build_chat_request(
        _cfg("azure", model="dep1", api_base="https://r.openai.azure.com",
             api_version="2024-06-01"),
        messages=[], stream=False, response_format=None, params={},
    )
    assert url == (
        "https://r.openai.azure.com/openai/deployments/dep1/chat/completions"
        "?api-version=2024-06-01"
    )
    assert headers["api-key"] == "k"
    assert "Authorization" not in headers


def test_structured_output_passthrough():
    schema = {"type": "json_schema", "json_schema": {"name": "R", "schema": {}}}
    _, _, body = build_chat_request(
        _cfg("openai"), messages=[], stream=False, response_format=schema, params={},
    )
    assert body["response_format"] == schema


def test_structured_output_pydantic_model_normalized():
    """A Pydantic model CLASS passed as response_format is expanded into the
    OpenAI json_schema wire body (mirrors what litellm does internally)."""
    from pydantic import BaseModel

    class ReportModel(BaseModel):
        title: str
        summary: str

    _, _, body = build_chat_request(
        _cfg("openai"), messages=[], stream=False,
        response_format=ReportModel, params={},
    )
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "ReportModel"
    schema = rf["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert "title" in schema["properties"] and "summary" in schema["properties"]


def test_embed_request_url_and_body():
    url, headers, body = build_embed_request(_cfg("openai"), inputs=["a", "b"])
    assert url == "https://api.openai.com/v1/embeddings"
    assert headers["Authorization"] == "Bearer k"
    assert body["input"] == ["a", "b"] and body["model"] == "m"
