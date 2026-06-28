# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""conversations + messages for multi-turn Q&A.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "kb_id", sa.Integer, sa.ForeignKey("knowledge_base.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_conversation_kb_updated", "conversation", ["kb_id", "updated_at"])
    op.create_table(
        "message",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("method", sa.String, nullable=True),
        sa.Column("rewritten_query", sa.Text, nullable=True),
        sa.Column("rewrite_fell_back", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("sources_json", sa.Text, nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("elapsed_ms", sa.Float, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_message_conv_ordinal", "message", ["conversation_id", "ordinal"])


def downgrade() -> None:
    op.drop_index("ix_message_conv_ordinal", table_name="message")
    op.drop_table("message")
    op.drop_index("ix_conversation_kb_updated", table_name="conversation")
    op.drop_table("conversation")
