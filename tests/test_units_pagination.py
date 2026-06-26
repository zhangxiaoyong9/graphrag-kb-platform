"""GET /steps/{id}/units is paginated: {items, total} with limit/offset + status filter."""

from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base, UnitKind  # noqa: F401
from kb_platform.db.repository import Repository


def _seed(tmp_path, n=12):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/u.db"))
    Base.metadata.create_all(repo.engine)
    with repo.engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO knowledge_base(name,method,settings_json,data_root) "
            "VALUES('k','standard','{}','.')"
        )
        c.exec_driver_sql(
            "INSERT INTO job(kb_id,type,method,status) "
            "VALUES(1,'full','standard','running')"
        )
        c.exec_driver_sql(
            "INSERT INTO step(job_id,name,ordinal,kind,status,attempt_no) "
            "VALUES(1,'extract_graph',1,'unit_fanout','running',0)"
        )
        for i in range(n):
            st = "failed" if i % 3 == 0 else "succeeded"
            c.exec_driver_sql(
                "INSERT INTO unit(step_id,subject_type,subject_id,kind,status,attempt_no) "
                f"VALUES(1,'chunk','c{i}','extract_graph','{st}',0)"
            )
    return repo, TestClient(create_app(repo, data_root="."))


def test_units_pagination_default(tmp_path):
    _, c = _seed(tmp_path, n=12)
    r = c.get("/steps/1/units")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 12
    # default limit=20 > 12 seeded, so all 12 returned
    assert len(body["items"]) == 12


def test_units_pagination_limit_offset(tmp_path):
    _, c = _seed(tmp_path, n=12)
    r = c.get("/steps/1/units?limit=5&offset=0")
    body = r.json()
    assert body["total"] == 12 and len(body["items"]) == 5
    r2 = c.get("/steps/1/units?limit=5&offset=10")
    body2 = r2.json()
    assert body2["total"] == 12 and len(body2["items"]) == 2  # last page


def test_units_pagination_status_filter_total(tmp_path):
    _, c = _seed(tmp_path, n=12)  # 4 failed (i%3==0: 0,3,6,9), 8 succeeded
    r = c.get("/steps/1/units?status=failed")
    body = r.json()
    assert body["total"] == 4
    assert all(it["status"] == "failed" for it in body["items"])
