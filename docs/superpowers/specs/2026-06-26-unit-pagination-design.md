# Unit 列表分页

> 状态：已 brainstorm、已确认；待写实现计划。
> 日期：2026-06-26。
> 范围：后端 units 端点分页 + 前端 UnitTable 分页。

## 1. 背景

`GET /steps/{id}/units?status=` 返回**全部** unit，`UnitTable` 一次渲染全部。大语料的 extract_graph 步骤可能有数百 unit → 一次性渲染慢、不好翻。`retry.py` 用 `repo.list_units(step_id)`（全量取失败 unit 做重置，与展示分页无关，不动）。

## 2. 目标 / 非目标

**目标**
- unit 列表分页：每页 20 条，上一页/下一页 + "第 X–Y 条 / 共 N 条"。
- status 过滤在分页前应用；total = 过滤后总数。
- 切换步骤/状态过滤 → 回第 1 页；2s 轮询刷新当前页。

**非目标**
- 不改 `retry.py`（它用 `list_units` 全量，保留）。
- 不改 unit 的其它字段/行为。
- 不做页码跳转输入（只上一页/下一页 + 指示）。

## 3. 设计

### 3.1 后端

`kb_platform/api/models.py`：新增
```python
class UnitPage(BaseModel):
    items: list[UnitOut]
    total: int
```

`kb_platform/db/repository.py`：新增（不动既有 `list_units`）
```python
def list_units_page(self, step_id: int, status: str | None, limit: int, offset: int) -> tuple[list[Unit], int]:
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
（`func` from sqlalchemy；`order_by(Unit.id)` 保证分页稳定。）

`kb_platform/api/routes_jobs.py`：改
```python
@router.get("/steps/{step_id}/units", response_model=UnitPage)
def get_units(step_id: int, request: Request, status: str | None = None, limit: int = 20, offset: int = 0) -> UnitPage:
    repo = request.app.state.repo
    items, total = repo.list_units_page(step_id, status, limit, offset)
    return UnitPage(items=[_unit_out(u) for u in items], total=total)
```
（`_unit_out(u)` 抽出既有 UnitOut 映射，复用。）

### 3.2 前端

`web/src/api/client.ts`：
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

`web/src/components/UnitTable.tsx`：
- state 加 `offset`（0 起）；`limit = 20` 常量。
- `reload()` 调 `getUnits(stepId, {status: filter || undefined, limit, offset})` → 存 `{items, total}`。
- 切换 stepId/filter → `setOffset(0)`（回到第 1 页）再 reload。
- 上一页/下一页按钮：`offset` 减/加 limit（clamp ≥0、不超过 total）；翻页后 reload。
- 底部分页栏："第 {offset+1}–{min(offset+limit,total)} 条 / 共 {total} 条" + 上一页（offset>0 时可用）/ 下一页（offset+limit<total 时可用）。
- 2s 轮询照旧（active 时刷新当前页）。

### 3.3 数据流

UnitTable(offset) → getUnits(stepId,{status,limit,offset}) → `GET /steps/{id}/units?status=&limit=20&offset=` → repo.list_units_page（SQL 过滤+分页+计数）→ `{items,total}` → 渲染当前页 + 分页栏。

## 4. 测试

- 后端：`GET /steps/{id}/units` 分页（limit/offset 生效；total 正确；status 过滤后 total 变化；offset 越界返回空 items + 正确 total）。
- 前端：UnitTable 渲染分页栏；翻页触发带 offset 的 getUnits（msw）；切换 status 回第 1 页。
- 既有测试：`routes_jobs` / `list_units`（retry 用）/ 既有 UnitTable 测试若依赖旧 `list` 响应需同步更新。
- 199→ 既有 + 新增；ruff/build 干净。

## 5. 风险

1. **响应形状 break（list → {items,total}）**：UnitTable 是唯一展示消费方（已确认）；retry.py 用 repo.list_units 不受影响；client + UnitTable 同步更新。
2. **分页与轮询**：轮询只刷新当前 offset 页，不翻页。
3. **offset 越界**：SQL offset 超过 total → 空列表，total 仍正确，UI 显示"第 0 条"→ 上一页回到末页（clamp）。

## 6. 改动清单

- 后端：`kb_platform/api/models.py`（UnitPage）、`kb_platform/db/repository.py`（list_units_page）、`kb_platform/api/routes_jobs.py`（端点改形 + _unit_out 抽取）。
- 前端：`web/src/api/client.ts`（UnitPage + getUnits）、`web/src/components/UnitTable.tsx`（分页）。
- 测试：扩展既有 units 端点测试 / UnitTable 测试。
