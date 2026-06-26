# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""add chunk ordinal composite index

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-27 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_chunk_kb_document_ordinal",
        "chunk",
        ["kb_id", "document_id", "ordinal"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chunk_kb_document_ordinal", table_name="chunk")
