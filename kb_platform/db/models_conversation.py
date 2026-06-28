# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Conversation + Message: persisted multi-turn Q&A (control plane).

Each conversation is bound to one KB; each assistant message carries its own
retrieval result (method, sources, tokens, the rewritten query) so a transcript
renders with the same richness as a single-shot query.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from kb_platform.db.models import Base


class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = (Index("ix_conversation_kb_updated", "kb_id", "updated_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_base.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (Index("ix_message_conv_ordinal", "conversation_id", "ordinal"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String)  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(String, nullable=True)
    rewritten_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewrite_fell_back: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
