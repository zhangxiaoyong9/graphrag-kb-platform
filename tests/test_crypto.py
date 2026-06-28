import json

from kb_platform.db import crypto
from kb_platform.db.crypto import encrypt_values, decrypt_values


def test_round_trip_uses_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("KB_SECRET_KEY", raising=False)  # force file path
    key_file = tmp_path / ".kb_secret_key"
    monkeypatch.setattr(crypto, "_key_file_path", lambda: str(key_file))
    crypto._fernet.cache_clear()
    token_json = encrypt_values(["sk-aaa", "sk-bbb"])
    tokens = json.loads(token_json)
    assert len(tokens) == 2
    assert all(t != "sk-aaa" and t != "sk-bbb" for t in tokens)  # tokens are not plaintext
    assert "sk-aaa" not in token_json
    assert key_file.exists()                 # auto-generated file created
    assert decrypt_values(token_json) == ["sk-aaa", "sk-bbb"]


def test_env_key_used_and_no_file(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("KB_SECRET_KEY", key)
    key_file = tmp_path / ".kb_secret_key"
    monkeypatch.setattr(crypto, "_key_file_path", lambda: str(key_file))
    crypto._fernet.cache_clear()
    token_json = encrypt_values(["sk-x"])
    assert decrypt_values(token_json) == ["sk-x"]
    assert not key_file.exists()  # env key means no file is created
