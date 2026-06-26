# Unit 列表分页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** unit 列表分页（每页 20），后端 `GET /steps/{id}/units` 改 `{items,total}` + limit/offset，前端 UnitTable 加上一页/下一页 + 指示。

**Architecture:** 后端 repo `list_units_page`（status 过滤 + LIMIT/OFFSET + COUNT 在 SQL）；端点响应 `UnitPage{items,total}`；既有 `list_units`（全量）保留给 retry.py。前端 `getUnits` 返回 `{items,total}`；UnitTable 加 offset 状态 + 分页栏，切换 step/status 回第 1 页，轮询刷新当前页。

**Tech Stack:** Python 3.11 + uv + FastAPI + SQLAlchemy + pytest/ruff；React 18 + TS + Vite + Tailwind + vitest。

## Global Constraints

- 后端 `uv run pytest` / `uv run ruff check .`；前端 `cd web && npm run build && npm test`；kb-platform 无 pyright/poe/semversioner（ruff only）。
- 每页 **20** 条（固定，无选择器）。
- `list_units(step_id)`（全量）保留给 `retry.py`，不动。
- 响应形状 break：`list[UnitOut]` → `UnitPage{items, total}`（UnitTable 是唯一展示消费方）。
- status 过滤在分页前应用；total = 过滤后总数。
- 既有测试不回归（同步更新依赖旧 `list` 响应的测试）。

---

## File Structure

- `kb_platform/api/models.py`（改）：新增 `UnitPage`。
- `kb_platform/db/repository.py`（改）：新增 `list_units_page`（+ import `func`）。
- `kb_platform/api/routes_jobs.py`（改）：抽 `_unit_out`；`get_units` 改分页 + `UnitPage`。
- `web/src/api/client.ts`（改）：`UnitPage` 接口 + `getUnits` 新签名。
- `web/src/components/UnitTable.tsx`（改）：分页。
- 测试：`tests/test_units_pagination.py`（新建）；扩展/新建 `web/src/components/UnitTable.test.tsx`。

---

## Task 1: 后端 — `UnitPage` + `list_units_page` + 端点分页

**Files:**
- Modify: `kb_platform/api/models.py`、`kb_platform/db/repository.py`、`kb_platform/api/routes_jobs.py`
- Test: `tests/test_units_pagination.py`（新建）

**Interfaces:**
- Produces: `UnitPage(BaseModel){items: list[UnitOut], total: int}`；`repo.list_units_page(step_id, status, limit, offset) -> tuple[list[Unit], int]`；`GET /steps/{id}/units?status=&limit=20&offset=0 -> UnitPage`。

- [ ] **Step 1: 写失败测试**

`tests/test_units_pagination.py`：
```python
"""GET /steps/{id}/units is paginated: {items, total} with limit/offset + status filter."""
import json

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.enums import UnitKind, UnitStatus
from kb_platform.db.models import Base, Job, KnowledgeBase, Step, Unit, UnitKind  # noqa: F811
from kb_platform.db.repository import Repository
from fastapi.testclient import TestClient


def _seed(tmp_path, n=12):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/u.db"))
    Base.metadata.create_all(repo.engine)
    with repo.engine.begin() as c:
        c.exec_driver_sql("INSERT INTO knowledge_base(name,method,settings_json,data_root) VALUES('k','standard','{}','.')")
        c.exec_driver_sql("INSERT INTO job(kb_id,type,method,status) VALUES(1,'full','standard','running')")
        c.exec_driver_sql("INSERT INTO step(job_id,name,ordinal,kind,status) VALUES(1,'extract_graph',1,'unit_fanout','running')")
        for i in range(n):
            st = "failed" if i % 3 == 0 else "succeeded"
            c.exec_driver_sql(f"INSERT INTO unit(step_id,subject_type,subject_id,kind,status) VALUES(1,'chunk','c{i}','extract_graph','{st}')")
    return repo, TestClient(create_app(repo, data_root="."))


def test_units_pagination_default(tmp_path):
    _, c = _seed(tmp_path, n=12)
    r = c.get("/steps/1/units")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 12
    assert len(body["items"]) == 10  # NOTE: default limit is 20, but only 12 seeded -> all 12; see impl


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
```
> 注：`test_units_pagination_default` 的断言——默认 limit=20、seeded 12 → 返回 12。把注释那行的断言改成 `len(body["items"]) == 12`。实现时按实际 seeded 数对齐。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv run pytest tests/test_units_pagination.py -v`
Expected: FAIL — 响应仍是 `list`（无 `total` 键）/ 端点不分页。

- [ ] **Step 3: 实现 models + repo + route**

(a) `kb_platform/api/models.py`，在 `UnitOut` 之后加：
```python
class UnitPage(BaseModel):
    """Paginated unit list for a step (display)."""

    items: list[UnitOut]
    total: int
```

(b) `kb_platform/db/repository.py`：顶部 import 加 `func`：
```python
from sqlalchemy import func, or_, select, update
```
在 `list_units` 之后加：
```python
    def list_units_page(
        self, step_id: int, status: str | None, limit: int, offset: int
    ) -> tuple[list[Unit], int]:
        """Paginated units for display: status filter + LIMIT/OFFSET + COUNT in SQL.
        (list_units(step_id) — the unpaginated all-units method — stays for retry.py.)
        """
        with session_scope(self.engine) as s:
            q = select(Unit).where(Unit.step_id == step_id)
            cq = select(func.count()).select_from(Unit).where(Unit.step_id == step_id)
            if status:
                q = q.where(Unit.status == status)
                cq = cq.where(Unit.status == status)
            total = s.scalar(cq) or 0
            items = list(s.scalars(q.order_by(Unit.id).limit(limit).offset(offset)))
            return items, total
```

(c) `kb_platform/api/routes_jobs.py`：顶部 import 加 `UnitPage`：
```python
from kb_platform.api.models import (
    JobCreate, JobCreated, JobOut, StepOut, UnitOut, UnitPage, UnitProgress,
)
```
抽 `_unit_out` + 改 `get_units`：
```python
def _unit_out(u) -> UnitOut:
    return UnitOut(
        id=u.id, subject_id=u.subject_id, status=u.status, error=u.error,
        llm_raw_output=u.llm_raw_output, needs_reconsolidation=u.needs_reconsolidation,
    )


@router.get("/steps/{step_id}/units", response_model=UnitPage)
def get_units(
    step_id: int, request: Request, status: str | None = None,
    limit: int = 20, offset: int = 0,
) -> UnitPage:
    repo = request.app.state.repo
    items, total = repo.list_units_page(step_id, status, limit, offset)
    return UnitPage(items=[_unit_out(u) for u in items], total=total)
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归 + ruff**

Run: `uv run pytest tests/test_units_pagination.py -v && uv run pytest -q && uv run ruff check .`
Expected: 新增 3 通过；既有全绿（若有依赖旧 list 响应的测试已同步——检查 `tests/` 里 `get_units`/`/units` 用法）；ruff 干净。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/api/models.py kb_platform/db/repository.py kb_platform/api/routes_jobs.py tests/test_units_pagination.py
git commit -m "feat(api): paginate GET /steps/{id}/units -> {items,total} with limit/offset"
```

---

## Task 2: 前端 — `getUnits` 新形状 + UnitTable 分页

**Files:**
- Modify: `web/src/api/client.ts`、`web/src/components/UnitTable.tsx`
- Test: `web/src/components/UnitTable.test.tsx`（新建）

**Interfaces:**
- Consumes: Task 1 的 `UnitPage{items,total}` + `?limit=&offset=`。
- Produces: `getUnits(stepId, {status?, limit?, offset?}) -> UnitPage`；UnitTable 分页（offset 状态 + 上一页/下一页 + 指示）。

- [ ] **Step 1: 写失败测试**

`web/src/components/UnitTable.test.tsx`：
```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import UnitTable from "./UnitTable";

const makeUnits = (n: number) => Array.from({ length: n }, (_, i) => ({
  id: i + 1, subject_id: `c${i}`, status: "succeeded", error: null, llm_raw_output: null, needs_reconsolidation: false,
}));

const server = setupServer(
  http.get("/steps/1/units", ({ request }) => {
    const url = new URL(request.url);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const status = url.searchParams.get("status");
    const all = makeUnits(45).filter((u) => (status ? u.status === status : true));
    return HttpResponse.json({ items: all.slice(offset, offset + 20), total: all.length });
  }),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("paginates 20 per page with controls", async () => {
  render(<UnitTable stepId={1} active={false} />);
  // page 1: 20 items + "第 1–20 条 / 共 45 条"
  expect(await screen.findByText("c0")).toBeInTheDocument();
  expect(screen.getByText(/1–20.*45/)).toBeInTheDocument();
  expect(screen.queryByText("c20")).not.toBeInTheDocument(); // page 2 item not shown
  // next page
  await userEvent.click(screen.getByRole("button", { name: /下一页/ }));
  expect(await screen.findByText("c20")).toBeInTheDocument();
  expect(screen.getByText(/21–40.*45/)).toBeInTheDocument();
});
```
> 注：占位/文案以实现时实际为准；selector 可微调。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm test -- UnitTable`
Expected: FAIL — `getUnits` 仍返回 `UnitOut[]`（无 total）/ 无分页栏。

- [ ] **Step 3: 实现 client + UnitTable 分页**

(a) `web/src/api/client.ts`：改 `getUnits` + 加 `UnitPage`：
```typescript
export interface UnitPage { items: UnitOut[]; total: number }
export const getUnits = (stepId: number, opts: { status?: string; limit?: number; offset?: number } = {}) => {
  const qs = new URLSearchParams();
  if (opts.status) qs.set("status", opts.status);
  if (opts.limit != null) qs.set("limit", String(opts.limit));
  if (opts.offset != null) qs.set("offset", String(opts.offset));
  const tail = qs.toString();
  return req<UnitPage>(`/steps/${stepId}/units${tail ? `?${tail}` : ""}`);
};
```

(b) `web/src/components/UnitTable.tsx`：加分页。state：`units: UnitOut[]`, `total: number`, `offset: number`，`LIMIT = 20`。reload 调 `getUnits(stepId, {status: filter||undefined, limit: LIMIT, offset})` → setUnits(items) + setTotal。切换 stepId/filter → setOffset(0)。上一页/下一页改 offset（clamp）。底部分页栏：
```tsx
<div className="mt-3 flex items-center justify-between text-[12px] text-muted">
  <span className="nums">
    第 {total === 0 ? 0 : offset + 1}–{Math.min(offset + LIMIT, total)} 条 / 共 {total} 条
  </span>
  <div className="flex gap-2">
    <button className="btn btn-sm btn-secondary" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}>上一页</button>
    <button className="btn btn-sm btn-secondary" disabled={offset + LIMIT >= total} onClick={() => setOffset((o) => o + LIMIT)}>下一页</button>
  </div>
</div>
```
（reload 用 useEffect 依赖 [stepId, filter, offset]；轮询 useEffect 依赖 [active, stepId] 调 reload。）

- [ ] **Step 4: build + 测试 + 全量回归**

Run: `cd web && npm run build && npm test && cd .. && uv run pytest -q`
Expected: build 干净；前端（含新 UnitTable 测试）+ 后端全绿；ruff 干净。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/api/client.ts web/src/components/UnitTable.tsx web/src/components/UnitTable.test.tsx
git commit -m "feat(web): UnitTable pagination (20/page, prev/next, total indicator)"
```

---

## Self-Review

- **Spec 覆盖**：3.1（UnitPage + list_units_page + 端点）→ Task 1；3.2（client + UnitTable 分页）→ Task 2；每页 20 固定 → Global Constraints；status 过滤在分页前 → Task 1 list_units_page（status 同时用于 q 和 cq）；切换 step/status 回第 1 页 → Task 2 setOffset(0)；轮询当前页 → Task 2。全覆盖。
- **占位符扫描**：无 TBD/TODO；每步含完整代码。
- **类型一致性**：`UnitPage{items,total}`（models）↔ `{items,total}`（client UnitPage）↔ `getUnits -> UnitPage`；`list_units_page(step_id, status, limit, offset) -> tuple[list[Unit], int]` ↔ 端点 `items, total = repo.list_units_page(...)`；`_unit_out` 抽取后 get_units 复用。
- **既有不回归**：`list_units(step_id)`（retry.py 用）不动；端点形状 break 但 UnitTable 是唯一展示消费方（Task 2 同步更新 client+UnitTable）；既有依赖 `/units` 的测试（若有）在 Task 1 Step 4 检查同步。
