"""Repository DAO for conversations + messages (in-memory, no LLM)."""
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return repo


def test_get_kb(tmp_path):
    repo = _setup(tmp_path)
    assert repo.get_kb(1) is not None and repo.get_kb(1).name == "kb1"
    assert repo.get_kb(999) is None


def test_create_and_get_conversation(tmp_path):
    repo = _setup(tmp_path)
    c = repo.create_conversation(1, title="t")
    assert c.kb_id == 1 and c.title == "t"
    assert repo.get_conversation(c.id).id == c.id
    assert repo.get_conversation(999) is None


def test_add_message_assigns_increasing_ordinals(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    a = repo.add_message(cid, role="user", content="q1").ordinal
    b = repo.add_message(cid, role="assistant", content="a1", method="local").ordinal
    c = repo.add_message(cid, role="user", content="q2").ordinal
    assert (a, b, c) == (0, 1, 2)


def test_get_and_recent_messages_ordering(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    for i, (role, txt) in enumerate([("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]):
        repo.add_message(cid, role=role, content=txt)
    rows = repo.get_messages(cid)
    assert [r.content for r in rows] == ["q1", "a1", "q2", "a2"]
    recent = repo.recent_messages(cid, limit=2)
    assert [r.content for r in recent] == ["q2", "a2"]  # ascending, last 2


def test_list_conversations_returns_snippet(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    repo.add_message(cid, role="user", content="hello")
    repo.add_message(cid, role="assistant", content="a long answer body", method="local")
    out = repo.list_conversations(1)
    assert len(out) == 1
    conv, snippet = out[0]
    assert conv.id == cid and snippet == "a long answer body"


def test_title_touch_delete(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    assert repo.update_conversation_title(cid, "renamed")
    assert repo.get_conversation(cid).title == "renamed"
    assert repo.update_conversation_title(999, "x") is False
    repo.touch_conversation(cid)  # no error
    repo.add_message(cid, role="user", content="q")
    assert len(repo.get_messages(cid)) == 1
    assert repo.delete_conversation(cid) is True
    assert repo.get_conversation(cid) is None
    assert repo.get_messages(cid) == []  # messages cascaded
    assert repo.delete_conversation(999) is False
