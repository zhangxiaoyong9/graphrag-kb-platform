import pytest

from kb_platform.query.graphrag_engine import GraphRagQueryEngine


def test_graphrag_engine_constructs():
    """GraphRagQueryEngine 可构造(真实 LLM 跑通留手动冒烟)。"""
    engine = GraphRagQueryEngine(data_root="/tmp", model_config=None)
    assert engine is not None


@pytest.mark.asyncio
async def test_global_returns_error_when_no_reports(tmp_path):
    """global 查询在无 community_reports 时优雅返回 error。"""
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1",
                method="standard",
                settings_json="{}",
                data_root=str(tmp_path),
            )
        )
    # 无 community_reports.parquet
    qe = GraphRagQueryEngine(data_root=str(tmp_path), model_config=None)
    result = await qe.search("global", "what?", str(tmp_path))
    assert result.error is not None and "community reports" in result.error.lower()


@pytest.mark.asyncio
async def test_drift_returns_error_when_no_reports(tmp_path):
    """drift 查询在无 community_reports 时优雅返回 error。"""
    qe = GraphRagQueryEngine(data_root=str(tmp_path), model_config=None)
    result = await qe.search("drift", "what?", str(tmp_path))
    assert result.error is not None and "community reports" in result.error.lower()


@pytest.mark.asyncio
async def test_local_without_reports_proceeds_past_guard(tmp_path):
    """local 查询不触发 reports-empty 守卫(会进入真实/失败路径,但不是 reports error)。"""
    qe = GraphRagQueryEngine(data_root=str(tmp_path), model_config=None)
    result = await qe.search("local", "what?", str(tmp_path))
    # 没有 index 数据 → 应进入异常路径,error 不应是 "community reports"
    if result.error is not None:
        assert "community reports" not in result.error.lower()
