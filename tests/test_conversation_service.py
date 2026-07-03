"""ConversationService: rewrite + retrieve + persist, above the QueryEngine."""
from kb_platform.conversation.rewriter import FakeRewriter, RewriteResult
from kb_platform.conversation.service import ConversationService, StreamEvent
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return repo


class _RecordingRewriter:
    """Records calls; returns a deterministic standalone so we can assert the
    engine received the rewritten query (FakeQueryEngine echoes the query)."""

    def __init__(self):
        self.calls = []

    async def rewrite(self, message, history):
        self.calls.append((message, [h.content for h in history]))
        return RewriteResult(standalone=f"REWRITTEN::{message}", prompt_tokens=5, output_tokens=2)


async def test_first_turn_passes_through_no_rewrite(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id

    class Boom:
        async def rewrite(self, m, h):
            raise AssertionError("rewriter must not run on the first turn")

    svc = ConversationService(repo, FakeQueryEngine(), Boom(), data_root=".")
    msg = await svc.send(cid, "What does Acme do?", None)
    assert msg is not None and msg.role == "assistant"
    assert "What does Acme do?" in msg.content  # FakeQueryEngine echoes the query
    assert msg.rewritten_query is None and msg.rewrite_fell_back is False
    rows = repo.get_messages(cid)
    assert [r.role for r in rows] == ["user", "assistant"]


async def test_follow_up_rewrites_and_carries_method_default(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    rw = _RecordingRewriter()
    svc = ConversationService(repo, FakeQueryEngine(), rw, data_root=".")
    await svc.send(cid, "What does Acme do?", "global")
    msg2 = await svc.send(cid, "who is the CEO?", None)
    assert len(rw.calls) == 1 and rw.calls[0][0] == "who is the CEO?"
    assert msg2.rewritten_query == "REWRITTEN::who is the CEO?"
    assert "REWRITTEN::who is the CEO?" in msg2.content  # reached the engine
    assert msg2.method == "global"  # defaulted from the prior assistant turn
    assert msg2.prompt_tokens is not None and msg2.prompt_tokens >= 5  # rewrite tokens merged


async def test_rewrite_failure_falls_back(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id

    class Fail:
        async def rewrite(self, m, h):
            raise RuntimeError("boom")

    svc = ConversationService(repo, FakeQueryEngine(), Fail(), data_root=".")
    await svc.send(cid, "first", "local")
    msg2 = await svc.send(cid, "next", None)
    assert msg2.rewrite_fell_back is True
    assert msg2.rewritten_query is None
    assert "next" in msg2.content  # engine answered using the raw message


async def test_skips_rewrite_when_rewriter_is_none(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), None, data_root=".")
    await svc.send(cid, "first", "local")
    msg2 = await svc.send(cid, "followup", None)
    assert msg2.rewritten_query is None and msg2.rewrite_fell_back is False
    assert "followup" in msg2.content


async def test_missing_conversation_returns_none(tmp_path):
    repo = _setup(tmp_path)
    svc = ConversationService(repo, FakeQueryEngine(), FakeRewriter(), data_root=".")
    assert await svc.send(999, "x", None) is None


async def test_auto_title_from_first_message(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), FakeRewriter(), data_root=".")
    await svc.send(cid, "Tell me everything about the Acme corporation", None)
    assert repo.get_conversation(cid).title.startswith("Tell me everything about the Acme")


async def _drain(gen) -> list[StreamEvent]:
    return [e async for e in gen]


async def test_send_streaming_first_turn_meta_delta_done(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), None, data_root=".")
    events = await _drain(svc.send_streaming(cid, "What does Acme do?", None))
    assert [e.type for e in events[:1]] == ["meta"]
    assert events[0].data["method"] == "local"
    assert "rewritten_query" not in events[0].data  # first turn: no rewrite
    deltas = [e for e in events if e.type == "delta"]
    assert deltas  # at least one streamed chunk
    terminals = [e for e in events if e.type in ("done", "error")]
    assert len(terminals) == 1 and terminals[0].type == "done"
    done = terminals[0]
    # done carries the persisted assistant message
    assert done.message.role == "assistant"
    assert "What does Acme do?" in done.message.content
    # persisted to DB exactly once (user + assistant)
    rows = repo.get_messages(cid)
    assert [r.role for r in rows] == ["user", "assistant"]


async def test_send_streaming_followup_rewrites(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    rw = _RecordingRewriter()
    svc = ConversationService(repo, FakeQueryEngine(), rw, data_root=".")
    await svc.send(cid, "What does Acme do?", "global")  # seed a turn
    events = await _drain(svc.send_streaming(cid, "who is the CEO?", None))
    meta = next(e for e in events if e.type == "meta")
    assert meta.data["rewritten_query"] == "REWRITTEN::who is the CEO?"
    assert meta.data["method"] == "global"  # defaulted from prior assistant
    done = next(e for e in events if e.type == "done")
    assert "REWRITTEN::who is the CEO?" in done.message.content


async def test_send_streaming_forwards_params_to_engine(tmp_path):
    """Chat path forwards the resolved params object to the engine."""
    from kb_platform.query.engine import QueryParams

    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    captured: list = []

    class _RecordingEngine(FakeQueryEngine):
        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            async for ev in super().stream_search(method, query, kb_data_root, params):
                yield ev

    svc = ConversationService(repo, _RecordingEngine(), None, data_root=".")
    await _drain(svc.send_streaming(cid, "hi", None, params=QueryParams(temperature=0.2)))
    assert captured and captured[0] is not None and captured[0].temperature == 0.2


async def test_send_streaming_defaults_params_none_when_omitted(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    captured: list = []

    class _RecordingEngine(FakeQueryEngine):
        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            async for ev in super().stream_search(method, query, kb_data_root, params):
                yield ev

    svc = ConversationService(repo, _RecordingEngine(), None, data_root=".")
    await _drain(svc.send_streaming(cid, "hi", None))
    assert captured and captured[0] is None  # no params passed -> engine sees None


def test_add_message_persists_cypher_and_truncated(tmp_path):
    """add_message persists cypher + truncated on the Message row (migration 0011 / ORM).

    Pure schema check — does NOT go through the service (that wiring is Task 2)."""
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    repo.add_message(
        cid, role="assistant", content="a",
        cypher="MATCH (n) RETURN n", truncated=True,
    )
    rows = repo.get_messages(cid)
    assistant = [r for r in rows if r.role == "assistant"][0]
    assert assistant.cypher == "MATCH (n) RETURN n"
    assert assistant.truncated is True


def test_query_preset_orm_carries_hops_and_timeout(tmp_path):
    """QueryPreset ORM accepts hops + cypher_timeout_ms (migration 0011 / ORM)."""
    repo = _setup(tmp_path)
    p = repo.create_query_preset(
        name="hyb", description="", method="hybrid",
        hops=3, cypher_timeout_ms=None,
    )
    assert p.hops == 3 and p.cypher_timeout_ms is None
    again = repo.get_query_preset(p.id)
    assert again is not None and again.hops == 3 and again.cypher_timeout_ms is None


async def test_send_streaming_emits_meta_cypher_and_persists(tmp_path):
    """When the engine yields StreamMeta, the service emits meta{cypher} and
    persists cypher + truncated on the assistant Message (carried in done.message)."""
    from kb_platform.query.engine import StreamDelta, StreamDone, StreamMeta

    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id

    class _CypherEngine:
        async def stream_search(self, method, query, kb_data_root, params=None):
            yield StreamMeta(cypher="MATCH (n) RETURN count(n)")
            yield StreamDelta(text="one ")
            yield StreamDelta(text="two")
            yield StreamDone(answer="one two", method=method, truncated=True)

    svc = ConversationService(repo, _CypherEngine(), None, data_root=".")
    events = await _drain(svc.send_streaming(cid, "how many?", "cypher"))

    metas = [e for e in events if e.type == "meta"]
    assert len(metas) == 2
    assert metas[0].data["method"] == "cypher"  # leading meta unchanged
    assert metas[1].data == {"method": "cypher", "cypher": "MATCH (n) RETURN count(n)"}

    done = next(e for e in events if e.type == "done")
    assert done.message.cypher == "MATCH (n) RETURN count(n)"
    assert done.message.truncated is True

    rows = repo.get_messages(cid)
    assistant = [r for r in rows if r.role == "assistant"][0]
    assert assistant.cypher == "MATCH (n) RETURN count(n)"
    assert assistant.truncated is True


async def test_send_streaming_without_meta_omits_cypher_and_not_truncated(tmp_path):
    """Engines that never yield StreamMeta (graphrag/Fake) emit only the leading
    meta, and the persisted row has cypher=None / truncated=False."""
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), None, data_root=".")
    events = await _drain(svc.send_streaming(cid, "hi", "local"))
    metas = [e for e in events if e.type == "meta"]
    assert len(metas) == 1 and "cypher" not in metas[0].data
    done = next(e for e in events if e.type == "done")
    assert done.message.cypher is None and done.message.truncated is False
