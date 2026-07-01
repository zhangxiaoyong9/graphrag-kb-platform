"""T12: ``KnowledgeBase.llm_fallback_profile_ids`` data-model contract.

This is a pure ORM round-trip test (no DB, no migration) — it only asserts the
column exists, accepts a JSON string, and defaults to NULL. The cross-profile
failover behaviour that *consumes* this column is exercised by T14.
"""
import json

from kb_platform.db.models import KnowledgeBase


def test_fallback_column_round_trips():
    kb = KnowledgeBase(
        name="x",
        llm_profile_id=1,
        llm_fallback_profile_ids=json.dumps([2, 3]),
    )
    assert json.loads(kb.llm_fallback_profile_ids) == [2, 3]


def test_fallback_column_nullable():
    kb = KnowledgeBase(name="x", llm_profile_id=1)
    # No fallback set → NULL → existing KBs behave exactly as today.
    assert kb.llm_fallback_profile_ids is None


def test_fallback_column_empty_list():
    # Explicit empty list must also be representable (== "no fallback").
    kb = KnowledgeBase(
        name="x",
        llm_profile_id=1,
        llm_fallback_profile_ids=json.dumps([]),
    )
    assert json.loads(kb.llm_fallback_profile_ids) == []
