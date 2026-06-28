# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""provider profiles + KB profile refs; backfill legacy KB settings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "provider_profile",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, unique=True),
        sa.Column("kind", sa.String),
        sa.Column("provider", sa.String),
        sa.Column("model", sa.String),
        sa.Column("api_base", sa.String, nullable=True),
        sa.Column("api_version", sa.String, nullable=True),
        sa.Column("api_keys_enc", sa.Text, nullable=False, server_default="[]"),
        sa.Column("structured_output", sa.Boolean, nullable=False, server_default=sa.text("1")),
    )
    op.add_column("knowledge_base", sa.Column("llm_profile_id", sa.Integer, nullable=True))
    op.add_column("knowledge_base", sa.Column("embedding_profile_id", sa.Integer, nullable=True))

    rows = bind.execute(
        sa.text("SELECT id, settings_json FROM knowledge_base WHERE llm_profile_id IS NULL")
    ).fetchall()
    seen: dict[tuple, int] = {}  # dedup key -> profile id
    name_counts: dict[str, int] = {}
    for kb_id, settings_json in rows:
        s = json.loads(settings_json or "{}")
        llm = s.get("llm") or {}
        emb = s.get("embedding") or {}
        reports = s.get("community_reports") or {}
        llm_pid = _profile(
            bind, seen, name_counts, kind="llm",
            provider=llm.get("model_provider", "openai"),
            model=llm.get("model", "gpt-4o-mini"),
            api_base=llm.get("api_base"), api_version=llm.get("api_version"),
            structured_output=bool(reports.get("structured_output", True)),
        )
        emb_pid = None
        if emb and emb.get("enabled", True):
            emb_pid = _profile(
                bind, seen, name_counts, kind="embedding",
                provider=emb.get("model_provider", "openai"),
                model=emb.get("model", "text-embedding-3-small"),
                api_base=emb.get("api_base"), api_version=emb.get("api_version"),
                structured_output=True,
            )
        for k in ("llm", "embedding"):
            s.pop(k, None)
        if "community_reports" in s:
            s["community_reports"].pop("structured_output", None)
        bind.execute(
            sa.text("UPDATE knowledge_base SET llm_profile_id=:p, embedding_profile_id=:e, "
                    "settings_json=:sj WHERE id=:i"),
            {"p": llm_pid, "e": emb_pid, "sj": json.dumps(s), "i": kb_id},
        )


def _profile(bind, seen, name_counts, *, kind, provider, model, api_base, api_version, structured_output):
    key = (kind, provider, model, api_base, api_version, structured_output)
    if key in seen:
        return seen[key]
    base = f"{provider}-{model}"
    name = base
    n = name_counts.get(base, 0)
    if n:
        name = f"{base}-{n}"
    name_counts[base] = n + 1
    res = bind.execute(
        sa.text(
            "INSERT INTO provider_profile (name,kind,provider,model,api_base,api_version,"
            "api_keys_enc,structured_output) VALUES (:n,:k,:p,:m,:ab,:av,'[]',:so)"
        ),
        {"n": name, "k": kind, "p": provider, "m": model,
         "ab": api_base, "av": api_version, "so": 1 if structured_output else 0},
    )
    pid = res.lastrowid
    seen[key] = pid
    return pid


def downgrade() -> None:
    op.drop_column("knowledge_base", "embedding_profile_id")
    op.drop_column("knowledge_base", "llm_profile_id")
    op.drop_table("provider_profile")
