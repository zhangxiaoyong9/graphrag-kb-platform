from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def _repo():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    return Repository(e), e


def test_seed_builtin_presets_present_after_create_all():
    # Alembic seeds via op.bulk_insert; for in-memory Base.metadata.create_all we
    # also seed in code (Repository.__init__ or a seed helper) so tests get them.
    repo, _ = _repo()
    names = {p.name for p in repo.list_query_presets()}
    assert {"默认", "简洁要点", "详尽调研"} <= names


def test_create_and_list_custom_preset():
    repo, _ = _repo()
    p = repo.create_query_preset(name="我的预设", method="local", community_level=1, temperature=0.2)
    assert p.id is not None and p.is_builtin is False
    assert any(x.name == "我的预设" for x in repo.list_query_presets())


def test_update_query_preset():
    repo, _ = _repo()
    p = repo.create_query_preset(name="p2", method="basic")
    updated = repo.update_query_preset(p.id, response_type="single paragraph")
    assert updated.response_type == "single paragraph"


def test_delete_query_preset():
    repo, _ = _repo()
    p = repo.create_query_preset(name="p3", method="local")
    assert repo.delete_query_preset(p.id) is True
    assert repo.get_query_preset(p.id) is None
