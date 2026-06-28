# A3 — 文档删除收敛（图收缩）— 设计文档

- 日期: 2026-06-28
- 状态: 已批准(待评审)
- 归属: A3（补齐设计承诺 · 增量索引收尾的"删文档"侧）
- 上游: 总体 spec `2026-06-24-kb-platform-design.md` §7（增量管道）/ §8（可观测性）；`CLAUDE.md` 约定"删除文档不收缩图 —— 重跑增量"；同系列 A2（`2026-06-28-update-clean-state-design.md`，"加文档"侧收尾）。
- 依赖: `atomic_steps.merge_delta`、`incremental.load_update_documents`、`repo.delete_document` / `create_job_pending` / `list_jobs_by_kb`、删除路由 `routes_kbs.delete_document`、A1 realtime（job 进度可见）。

## 1. 背景与目标

`CLAUDE.md` 写"删除文档只移除行 + chunk，**不收缩图** —— 重跑增量"。调查代码后发现这句话**今天并不成立**，是一个隐蔽的正确性 bug：

- 删除走 `Repository.delete_document`：删 `Chunk` 行 + `Document` 行，**就此结束**（`repository.py:100-114`）。
- 实体/关系由 `atomic_steps.merge_delta` 产出：它 `glob` 全部 `data_root/extractions/*.json`（按 `chunk_id` 命名）重新合并（`atomic_steps.py:37-56`）。
- 删除文档时，对应 chunk 的 `extractions/<chunk_id>.json` **没被清掉** → 重跑增量时 `merge_delta` 又把它们合回来 → **删除文档的实体/关系原样保留**。即"重跑增量"并不收缩图。

> 注：**全量路径其实没这个 bug**。`extract_graph.finalize` 只合并"当前 step 的 SUCCEEDED units"（`extract_graph.py:54-64`）；删除的 chunk 无 `Chunk` 行 → 无 unit → 自然排除。问题只出在**增量路径** `merge_delta` 的"glob 全部"。

向量侧**无需新增 delete 能力**：`LanceDBVectorStoreWrapper.upsert` 用 `create_index(mode="overwrite")` 整表重建（`vector_store.py:79-97`），重跑 `generate_text_embeddings` 自然丢掉 stale 向量。`text_units.parquet` 由 `update_clean_state` 从 chunk 表重建（A2），删完自动干净。

**因此真正的收缩 = 让 `merge_delta` 只合并"chunk 表里仍存在的 chunk"的 extraction + 重跑增量。** 零新增 LLM（`community_reports` 那步除外：只对成员变化的社区重生成，沿用既有 `CommunityReportsDeltaStrategy` + `reports_by_hash` carry-over，`delta.py:85-162`）。

**触发模型（已与用户确认）：** 删除文档即**自动**起一个增量重索引 job（选项 A）；该 KB 若已有 pending/running 的增量 job 则**合并**，不另起（选项 A1 —— 那个 job 会在 `merge_delta` 自愈）。

**成功标准:**
- 删除一份文档 → 自动起增量 job（若该 KB 已索引过且无在跑增量 job）→ job 完成后，该文档独有的实体/关系从 `entities.parquet`/`relationships.parquet` 消失；被存活 chunk 也抽到的同名实体保留。
- 向量库（entity/text_unit/community 三表）不再含删除文档的 stale 向量；`text_units.parquet` 不含其 chunk。
- 连删多份文档只起 1 个 job（合并）；删除时正有增量 job 在跑则不另起，靠在跑 job 收尾收缩。
- 从未索引过的 KB 删除文档：只清行，不起 job（没图可缩）。
- 删到一份文档不剩：产出空图（空但 schema 正确的 parquet），各 step 不崩。
- 无新增 DB 字段、无 alembic 迁移、无新增 Python/npm 依赖。

## 2. 范围（YAGNI）

**做:**
- `merge_delta` 按 chunk 表过滤 extractions，并 best-effort 清理孤儿 `extractions/<chunk_id>.json`。
- 删除路由：删行后，满足条件则自动 `create_job_pending(type="incremental")`；带合并守卫 + 从未索引守卫。
- 删除接口语义：起 job 时 `202 + JobCreated{id,status}`，否则 `204`。
- 前端：删除成功后刷新该 KB 的 jobs（realtime/A1 已让 shrink job 进度可见）。

**不做:**
- 新增 job type / 新增 atomic step / 改 `plan_incremental`（复用现有增量管道）。
- 新增 DB 列 / `graph_dirty` 标记（合并判断靠"是否已有 pending/running 增量 job"的纯查询，无需持久化脏标记）。
- `VectorStore` 加 `delete` 方法（overwrite-mode upsert 已覆盖）。
- 删除防抖/定时器（A1 合并已足够批量化）。
- "丢失贡献 chunk 的实体"的 `summarize_descriptions` 描述重算 —— 沿用既有增量近似（`SummarizeDeltaStrategy` 只对 description set 变化的实体重算，`delta.py:24-41`），列为已知次要限制。
- 删除接口的批量删除（一次一份，沿用现状）。

## 3. 后端设计

### 3.1 `merge_delta`：按 chunk 表过滤 + 清理孤儿 extractions（核心修复）

`atomic_steps.merge_delta` 改为先用 chunk 表对齐"哪些 extraction 仍有效"：

```python
def merge_delta(repo, adapter, step):
    from kb_platform.graph.adapter import ExtractionResult

    root = _data_root(repo, step)
    job = repo.get_job(step.job_id)
    live = {c.chunk_id for c in repo.get_chunks(job.kb_id)}  # 当前控制面真相
    extraction_dir = root / "extractions"
    results = []
    if extraction_dir.exists():
        for p in sorted(extraction_dir.glob("*.json")):
            if p.stem not in live:
                p.unlink(missing_ok=True)  # 孤儿：对应 chunk 已删，永不再有效
                continue
            raw = json.loads(p.read_text())
            results.append(ExtractionResult(
                entities=pd.DataFrame(raw["entities"]),
                relationships=pd.DataFrame(raw["relationships"]),
            ))
    entities, relationships = adapter.merge_extractions(results)
    entities.to_parquet(root / "entities.parquet")
    relationships.to_parquet(root / "relationships.parquet")
```

要点:
- **过滤保证正确**：删除文档的 chunk 不在 `live` → 其 extraction 既不加载、文件也被清 → `merge_extractions` 自然不再产出其独有实体。被存活 chunk 也抽到的同名实体，由存活 chunk 的 extraction 继续贡献，保留无误。
- **清理是 best-effort**：`unlink(missing_ok=True)` 包在 `if extraction_dir.exists()` 与 glob 里，单文件失败不应拖垮 merge（可再包一层 try/except 记日志，与 `write_kb_stats` 的容错风格一致）。即便清理失败，**过滤本身已保证正确**；残留孤儿文件只是磁盘冗余，下次 merge 再清。
- **不违反"extractions/ 不能删"**：那条约定针对"不要整目录删除 / 不要删仍有效的 extraction"（`reconsolidate` 依赖它吸收迟到单元，`CLAUDE.md`）。这里只删**chunk 已不存在**的孤儿文件，安全。
- **full 路径无需改动**：`extract_graph.finalize` 本就按 step units 过滤（见 §1）。孤儿文件在全量后由下次增量的 `merge_delta` 顺手清。

### 3.2 删除路由：自动起增量 job（A）+ 合并（A1）+ 从未索引守卫

`routes_kbs.delete_document` 在删行后决定是否起 job：

```python
@router.delete("/kbs/{kb_id}/documents/{doc_id}")
def delete_document(kb_id: int, doc_id: int, request: Request, response: Response):
    repo = request.app.state.repo
    if not repo.delete_document(kb_id, doc_id):
        raise HTTPException(404)
    job = _maybe_create_shrink_job(repo, kb_id)
    if job is not None:
        response.status_code = 202
        return JobCreated(id=job.id, status=job.status)
    return None  # 204
```

辅助函数（路由模块内私有；`JobStatus` 来自 `db.enums`）:

```python
def _maybe_create_shrink_job(repo, kb_id):
    jobs = repo.list_jobs_by_kb(kb_id)  # 返回 Job ORM 对象，含 .type / .status
    # A1 合并：已有 pending/running 增量 job → 不另起（那个 job 的 merge_delta 会自愈）
    if any(j.type == "incremental" and j.status in (JobStatus.PENDING, JobStatus.RUNNING) for j in jobs):
        return None
    # 从未索引守卫：该 KB 无任何 SUCCEEDED job → 没图可缩，不起 job
    if not any(j.status == JobStatus.SUCCEEDED for j in jobs):
        return None
    # 否则（含"在跑的是 full job"）→ 创建增量 job，排队其后兜底收缩
    return repo.create_job_pending(kb_id=kb_id, method="standard", type="incremental")
```

守卫语义（两个 `return None` 互不冲突，顺序无关）:
- **A1 合并**：已有 pending/running **增量** job → 不另起。那个 job 的 `merge_delta`（§3.1）会 reconcile 这次删除。连删 N 份 → 折叠成 1 个 job。
- **从未索引守卫**：该 KB 无任何 SUCCEEDED job → 删除只清行，不起 job（强起会在无图状态下空跑，浪费且语义错）。
- **full job 在跑时仍起增量 job**：若 pending/running 的是 **full** job（非 incremental），上面两条都不命中 → 落到 `create_job_pending`，创建增量 job 排队其后。理由：在跑的 full job 可能在删除前已建好 extract units，未必反映这次删除；排队其后的增量 job 的 `merge_delta` 兜底收缩。最终一致。

> `repo.list_jobs_by_kb` 返回 Job ORM 对象（含 `.type`/`.status`），路由内可直接判断，**无需改 `JobListItem`**。

### 3.3 删到空 / 空图：优雅处理

删掉最后一份文档 → `live = set()` → `merge_delta` 加载 0 个 extraction → `merge_extractions([])` 返回**空但 schema 正确**的 entities/relationships（`adapter.py:135-167` 已确认）。下游:
- `finalize_graph`：读空 parquet → `finalize_entities_relationships(空, 空)` → 须返回空（验证 FakeGraphAdapter / graphrag adapter 对空输入的行为，必要时短路）。
- `create_communities`：`cluster_relationships(空 relationships)` → 空 communities。
- `community_reports`：无社区 → 无 unit → step 空 SUCCEEDED。
- `update_clean_state`：`repo.get_chunks` 为空 → `write_text_units_parquet` 现有"无 chunk 则 no-op"（`atomic_steps.py:69-70`）→ **需改为写空 parquet**（否则删空后 `text_units.parquet` 残留旧数据，embeddings 仍向量化已删 chunk）。这是 A3 暴露的一个连带小修。
- `generate_text_embeddings`：三表 overwrite 空内容 → stale 向量清空。

> §3.3 的"空输入短路"是验证重点（见 §6），确保 FakeGraphAdapter 与 graphrag adapter 在空 DataFrame 下都不抛错。

## 4. API 设计

`DELETE /kbs/{kb_id}/documents/{doc_id}`:
- 文档不存在 / 不属该 KB → `404`（不变）。
- 删行成功 + 起了 shrink job → **`202` + `JobCreated{id,status}`**（异步；前端可据此跳到 job 详情 / realtime 看进度）。
- 删行成功 + 未起 job（从未索引 / 已有增量 job 在跑）→ **`204` 无 body**（删除本身已完成）。

无新增端点。shrink job 复用 `GET /kbs/{id}/jobs` / `GET /jobs/{id}` / WS 实时（A1）。

## 5. 前端设计

- 文档列表删除按钮：`204` 时移除该行（现状）；`202` 时移除该行 + 触发该 KB 的 jobs 刷新（让 shrink job 出现在列表 / realtime 视图）。
- 删除二次确认文案补一句中文，提示"将自动重建图谱（增量）"（与现有中文 copy 风格一致）。
- 不新增 badge / 不新增页面：shrink job 的可见性完全由 A1 realtime + jobs 列表覆盖。

## 6. 测试策略

**后端（`tests/`）:**
1. `merge_delta` 过滤 + 清理：预置 3 个 chunk 的 extraction 文件，删掉其中 1 个 chunk 行 → merge 后 entities 不含该 chunk 独有实体；该 `extractions/<id>.json` 被删；其余 2 个文件保留。
2. `merge_delta` 孤儿清理 best-effort：某文件 `unlink` 失败（mock）→ 不抛、merge 仍正确（过滤兜底）。
3. `merge_delta` 全空：`live=set()` → 空 entities/relationships parquet（schema 正确），无异常。
4. 删到空图端到端（FakeGraphAdapter 跑增量 job）：最后一份文档删除 → 自动 job → 空 entities/relationships/communities + 空 `text_units.parquet`（验证 `write_text_units_parquet` 改动）+ 向量表清空。
5. 删除路由自动起 job：已索引 KB 删文档 → `202` + `JobCreated`，且确实创建了一个 pending incremental job。
6. 从未索引守卫：无 SUCCEEDED job 的 KB 删文档 → `204`，无 job 创建。
7. A1 合并：已有一个 pending/running incremental job 时删文档 → `204`，**不**新建 job。
8. full-job 在跑：pending full job 时删文档 → `202`，**另建** incremental job（排队其后）。
9. 回归：既有增量测试全绿（`merge_delta` 行为变化不破坏加文档增量；`reconsolidate` 路径仍正确，因其 extractions 都对应存活 chunk）。

**前端（`web/`）:**
10. 文档列表删除：mock `DELETE` 返回 `202` → 行移除 + jobs 刷新被调用；返回 `204` → 仅移除行。

**E2E:** 跳过（单测 + 路由测已覆盖；真 LLM 冒烟可选，沿用既有 verify 流程）。

## 7. 非目标 / 延后项

- summarize 对"失贡献实体"的描述重算（§2，已知次要限制）。
- 批量删除接口 / 删除防抖（§2）。
- 跨 KB 的删除收敛（不适用）。
- 全量路径的孤儿文件即时清理（靠下次增量 `merge_delta` 顺手清，无需全量也插 reconcile）。
