"""T13: ``assemble_kb_settings`` packs the full ordered fallback list into
``llm.kb_profiles``.

Builds a fake KB ORM + a fake repo whose ``get_profile`` returns provider-profile
objects for the primary and fallback ids; asserts the resulting
``settings["llm"]["kb_profiles"]`` is ``[primary, *fallbacks]`` in failover order,
each with its own provider/model/keys, and that missing profiles / empty keys
raise ``ValueError`` (no silent degradation).
"""
import json

import pytest

from kb_platform.graph.graphrag_adapter import assemble_kb_settings


class _Profile:
    """Minimal ProviderProfile stand-in (matches the ORM attributes read)."""

    def __init__(self, *, id, name, provider, model, api_keys_enc, api_base=None,
                 api_version=None, structured_output=True, ssl_verify=True):
        self.id = id
        self.name = name
        self.provider = provider
        self.model = model
        self.api_base = api_base
        self.api_version = api_version
        self.api_keys_enc = api_keys_enc
        self.structured_output = structured_output
        self.ssl_verify = ssl_verify


class _FakeRepo:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_profile(self, pid):
        return self._profiles.get(pid)


class _KB:
    def __init__(self, *, llm_profile_id, fallback_ids=None, settings_json="{}",
                 data_root="."):
        self.id = 1
        self.llm_profile_id = llm_profile_id
        self.llm_fallback_profile_ids = (
            json.dumps(fallback_ids) if fallback_ids is not None else None
        )
        self.embedding_profile_id = None
        self.settings_json = settings_json
        self.data_root = data_root


def _enc(keys):
    """Fernet-encrypt a key list the same way ProviderProfile storage does."""
    from kb_platform.db.crypto import encrypt_values

    return encrypt_values(list(keys))


def test_kb_profiles_packs_primary_then_fallbacks_in_order():
    profiles = {
        1: _Profile(id=1, name="primary", provider="openai", model="gpt-4o-mini",
                    api_keys_enc=_enc(["pk-1"])),
        2: _Profile(id=2, name="fb-deepseek", provider="deepseek",
                    model="deepseek-chat", api_keys_enc=_enc(["dk-2a", "dk-2b"])),
        3: _Profile(id=3, name="fb-ollama", provider="ollama", model="llama3",
                    api_base="http://localhost:11434", api_keys_enc=_enc(["ollama"])),
    }
    kb = _KB(llm_profile_id=1, fallback_ids=[2, 3])
    settings = assemble_kb_settings(kb, _FakeRepo(profiles))

    prof = settings["llm"]["kb_profiles"]
    assert len(prof) == 3
    # order: primary first, then fallbacks in declared order
    assert prof[0]["provider"] == "openai"
    assert prof[0]["model"] == "gpt-4o-mini"
    assert prof[0]["keys"] == ["pk-1"]
    assert prof[1]["provider"] == "deepseek"
    assert prof[1]["model"] == "deepseek-chat"
    assert prof[1]["keys"] == ["dk-2a", "dk-2b"]
    assert prof[2]["provider"] == "ollama"
    assert prof[2]["model"] == "llama3"
    assert prof[2]["api_base"] == "http://localhost:11434"
    assert prof[2]["keys"] == ["ollama"]
    # primary keys still exposed at the top level for downstream readers
    assert settings["llm"]["api_keys"] == ["pk-1"]


def test_kb_profiles_primary_only_when_no_fallbacks():
    profiles = {
        1: _Profile(id=1, name="primary", provider="openai", model="gpt-4o-mini",
                    api_keys_enc=_enc(["pk-1"])),
    }
    kb = _KB(llm_profile_id=1, fallback_ids=None)
    settings = assemble_kb_settings(kb, _FakeRepo(profiles))
    prof = settings["llm"]["kb_profiles"]
    assert len(prof) == 1
    assert prof[0]["provider"] == "openai"


def test_kb_profiles_empty_fallback_list_is_primary_only():
    profiles = {
        1: _Profile(id=1, name="primary", provider="openai", model="gpt-4o-mini",
                    api_keys_enc=_enc(["pk-1"])),
    }
    kb = _KB(llm_profile_id=1, fallback_ids=[])
    settings = assemble_kb_settings(kb, _FakeRepo(profiles))
    assert len(settings["llm"]["kb_profiles"]) == 1


def test_missing_fallback_profile_raises():
    profiles = {
        1: _Profile(id=1, name="primary", provider="openai", model="gpt-4o-mini",
                    api_keys_enc=_enc(["pk-1"])),
        # id 2 intentionally absent
    }
    kb = _KB(llm_profile_id=1, fallback_ids=[2])
    with pytest.raises(ValueError, match="fallback provider profile 2 not found"):
        assemble_kb_settings(kb, _FakeRepo(profiles))


def test_fallback_with_empty_keys_raises():
    profiles = {
        1: _Profile(id=1, name="primary", provider="openai", model="gpt-4o-mini",
                    api_keys_enc=_enc(["pk-1"])),
        2: _Profile(id=2, name="fb-empty", provider="deepseek",
                    model="deepseek-chat", api_keys_enc=_enc([])),
    }
    kb = _KB(llm_profile_id=1, fallback_ids=[2])
    with pytest.raises(ValueError, match="has no API keys"):
        assemble_kb_settings(kb, _FakeRepo(profiles))
