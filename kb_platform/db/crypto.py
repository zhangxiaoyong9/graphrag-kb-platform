# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Fernet encryption for provider-profile API keys.

Master key source: env ``KB_SECRET_KEY`` if set; otherwise an auto-generated
key persisted to a file next to the DB (``<dirname(db_path)>/.kb_secret_key``,
chmod 600). The file path is resolved lazily from the configured DB url.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from cryptography.fernet import Fernet

_KEY_FILE_NAME = ".kb_secret_key"


def _key_file_path() -> str:
    """Resolve the master-key file path from the configured DB url.

    Defaults to ``./.kb_secret_key`` when no DB url is discoverable. Tests
    monkeypatch this function.
    """
    db_url = os.environ.get("KB_DB_URL") or "kb.db"
    path = db_url.replace("sqlite:///", "")
    return os.path.join(os.path.dirname(os.path.abspath(path)) or ".", _KEY_FILE_NAME)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    env_key = os.environ.get("KB_SECRET_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode())
    path = _key_file_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Fernet(f.read().strip())
    key = Fernet.generate_key()
    with open(path, "wb") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return Fernet(key)


def encrypt_values(values: list[str]) -> str:
    """Encrypt a list of plaintext strings -> JSON array of Fernet tokens."""
    if not values:
        return "[]"
    f = _fernet()
    return json.dumps([f.encrypt(v.encode()).decode() for v in values])


def decrypt_values(token_json: str) -> list[str]:
    """Decrypt a JSON array of Fernet tokens -> list of plaintext strings."""
    tokens = json.loads(token_json or "[]")
    if not tokens:
        return []
    f = _fernet()
    return [f.decrypt(t.encode()).decode() for t in tokens]


def master_key_source() -> str:
    return "env:KB_SECRET_KEY" if os.environ.get("KB_SECRET_KEY", "").strip() else _key_file_path()
