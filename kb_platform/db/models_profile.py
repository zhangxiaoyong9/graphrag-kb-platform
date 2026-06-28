# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""ProviderProfile: reusable LLM/embedding connection + encrypted API keys."""
from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from kb_platform.db.models import Base


class ProviderProfile(Base):
    __tablename__ = "provider_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)  # "llm" | "embedding"
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    api_base: Mapped[str | None] = mapped_column(String, nullable=True)
    api_version: Mapped[str | None] = mapped_column(String, nullable=True)
    api_keys_enc: Mapped[str] = mapped_column(Text, default="[]")
    structured_output: Mapped[bool] = mapped_column(Boolean, default=True)
