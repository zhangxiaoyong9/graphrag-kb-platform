# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Add provider_profile.ssl_verify (NOT NULL, server_default true).

Task 1 added the ORM column with a Python-side ``default=True``; that does NOT
backfill existing rows in real (Alembic-managed) databases, nor does it emit a
server default. This migration adds the column with ``server_default true`` so
existing profiles backfill securely (SSL verification on by default) and the
column is NOT NULL.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "provider_profile",
        sa.Column("ssl_verify", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("provider_profile", "ssl_verify")
