# A2 — `update_clean_state` 增量收尾 + KB 图谱规模 stats — 设计文档

- 日期: 2026-06-28
- 状态: 已批准(待评审)
- 归属: A2（补齐设计承诺 · 增量索引收尾 + 可观测性）
- 上游: A1 设计(`2026-06-28-websocket-realtime-progress-design.md`)末尾"后续: A2(`update_clean_state` 增量收尾) 另起 spec";总体 spec `2026-06-24-kb-platform-design.md` §7 步骤 10 / §8 可观测性。
- 依赖: 现有增量管道(`orchestrator.plan_incremental`、`atomic_steps.merge_delta`、`incremental.load_update_documents`)、`Repository.get_chunks`、KB 概览页(`KbOverviewPage`)。

## 1. 背景与目标

总体设计 §7 把增量管道的第 10 步命名为 `update_clean_state`,职责"合并 context.json/stats.json"。但:

1. **该步目前是空操作**(`orchestrator._run_atomic` 里 `elif step.name == "update_clean_state": pass  # MVP:空操作`)。
2. **`context.json` / `stats.json` 在当前代码里根本不存在** —— 数据面是 parquet(entities/relationships/communities/community_reports/text_units)+ LanceDB 向量 + `extractions/` 缓存。原设计的文件名不能照字面实现。

调查中确认了一个**真实的正确性缺口**:增量 job 的 `load_update_documents` 只把新 chunk 行写进 SQLite,**从不更新 `text_units.parquet`**;而 `generate_text_embeddings` 读 `text_units.parquet`(`atomic_steps.py:90-91`)建 `text_unit_text` 向量索引。后果:**增量索引加入的新文档,其 chunk 文本永远不会被向量化** → local 检索(按 text unit)静默漏掉这些新文档。`update_clean_state`(空操作,在 plan_incremental 里正好排在 `generate_text_embeddings` 之前)恰好是补这个缺口的位置。

本 spec 同时补齐"图谱规模"可观测性:每个 job 成功后写一份 `stats.json` 快照(实体/关系/社区/社区报告/chunk/文档计数),概览页展示 —— 这是原设计"stats.json"在当前架构下的合理落地。

**成功标准:**
- 增量 job 完成后,新文档的 chunk 文本出现在 `text_units.parquet` 并被向量化;local 检索能命中新文档。
- 任意 job(full / 增量)成功后,`<data_root>/stats.json` 反映最新图谱规模;概览页"图谱规模"卡显示这些计数。
- `update_clean_state` 是真步骤(失败则 job 失败);stats 写入是 best-effort(永不拖垮 job)。
- 无新增 DB 字段、无新增 alembic 迁移、无新增 Python/npm 依赖;worker 行为仅在 `orchestrator.run` 收尾处增量变化。

## 2. 范围(YAGNI)

**做:**
- `update_clean_state` 步:从 chunk 表重建 `text_units.parquet`(老+新)。
- `data_root/stats.json`:每 job 成功后写的图谱规模快照。
- `GET /kbs/{kb_id}/stats` 端点读它。
- 概览页"图谱规模"卡展示。

**不做:**
- 实时算 stats(已选时点快照,非 live parquet 读)。
- DB 列存 stats(已选文件)。
- 清理 `delta_manifest.json` / `extractions/` 缓存(`extractions/` 是 `reconsolidate` 的依赖,**不能删**;`delta_manifest.json` 每次 `load_update_documents` 覆盖,不构成 stale)。
- KB 列表页展示 stats(只做概览页)。
- 文档删除导致的图收缩(沿用既有"重跑增量"约定)。

## 3. 后端设计

### 3.1 `update_clean_state` 步:重建 `text_units.parquet`

新增 `atomic_steps.update_clean_state(repo, adapter, step)`:
- 经 `_data_root(repo, step)` 解析 KB 的 `data_root`。
- 从 `repo.get_chunks(job.kb_id)` 读该 KB 的**全部** chunk 行。
- 调共享 helper `write_text_units_parquet(data_root, chunks)` 写出 parquet。

**共享 helper(顺手重构,消除重复):** 把 `_chunk_documents`(`orchestrator.py:205-220`)里写 `text_units.parquet` 的那段抽成 `atomic_steps.write_text_units_parquet(data_root, chunks)`,产出 DataFrame 列不变(`id` / `text` / `document_ids` / `n_tokens=0`)。full 路径(`_chunk_documents`)与增量路径(`update_clean_state`)都调它 → 两路产物同形,无重复逻辑。

**接线:** `orchestrator._run_atomic` 把:
```python
elif step.name == "update_clean_state":
    pass  # MVP:空操作(state 合并留后续)
```
换成:
```python
elif step.name == "update_clean_state":
    atomic_steps.update_clean_state(self.repo, self.adapter, step)
```

**为何是步、而非收尾钩子:** 它必须在 `generate_text_embeddings`(下一步)之前完成,且要进 step 时间线(可观测、可重试)。full job 没这步,但 full 的 `_chunk_documents` 已写 `text_units.parquet`,故 full 不受影响。

**失败语义:** 该步抛错 → 步 `FAILED` → job `FAILED`(正确:缺 text_units 会坏 embeddings,不该静默)。

### 3.2 KB 图谱规模 stats(`data_root/stats.json`)

新增 `kb_platform/engine/kb_stats.py::write_kb_stats(repo, kb_id)`:
- 内部读 KB 行解析 `data_root`(同 `atomic_steps._data_root` 套路,签名只收 `kb_id`)。
- 计数来源:
  - **parquet 行数**:`entities.parquet` / `relationships.parquet` / `communities.parquet` / `community_reports.parquet` / `text_units.parquet`(文件缺失 → 该项 0)。
  - **DB 计数**:`document` 行数、`chunk` 行数(经 `Repository`)。
- 写 `<data_root>/stats.json`,形状:
  ```json
  { "updated_at": "2026-06-28T12:00:00",
    "document_count": 12, "chunk_count": 340,
    "entity_count": 1820, "relationship_count": 4100,
    "community_count": 64, "community_report_count": 60,
    "text_unit_count": 340 }
  ```
  (`updated_at` 用 UTC ISO8601;`text_unit_count` 来自 parquet 行数,与 chunk_count 一般相等但来源不同,分开存便于核对。)

**触发点:** `orchestrator.run` 成功收尾处,full + 增量都跑,位置在 `reconsolidate` **之后**(迟到重整合数据也计入):
```python
self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
if job.type == "incremental":
    await reconsolidate(self.repo, self.adapter, job.kb_id, self.data_root)
try:
    write_kb_stats(self.repo, job.kb_id)
except Exception:
    logger.exception("write_kb_stats failed for kb %s; stats may be stale", job.kb_id)
```

**best-effort:** 整个 `write_kb_stats` 内部对每个 parquet 读取做容错(缺失→0),且调用点再包一层 try/except + 日志。**stats 是可观测性,绝不因它失败让 job 挂。**

### 3.3 为何 stats 在收尾钩子、而非 `update_clean_state` 步

`update_clean_state` 只在 `plan_incremental` 里 → 若 stats 塞进这步,**full job 永远没 stats**(而全量首次索引恰恰最想看计数)。故 stats 走 `orchestrator.run` 收尾钩子(full + 增量一致),`update_clean_state` 步只管 text_units 重建。两个关注点分开,各自边界清晰。

## 4. API 设计

`GET /kbs/{kb_id}/stats` —— 加到 `routes_kbs.py`:
- 解析 KB 的 `data_root` → 读 `stats.json` → 返回其内容。
- **文件不存在 → 返回空对象 `{}`(不 404)**:概览页优雅降级(未索引过的 KB 显示"—")。
- 响应模型 `KbStatsOut`(全部字段 `int | None`,宽松;直接 `dict` 返回亦可,但用模型更显式)。

## 5. 前端设计

- `web/src/api/client.ts`:加 `getKbStats(kbId): Promise<KbStats>` 与 `KbStats` 类型(字段同 stats.json,全 optional)。
- `KbOverviewPage.tsx`:用 `useAsync(() => getKbStats(kbId), [kbId])` 加一张"图谱规模"卡,展示 实体 / 关系 / 社区 / 社区报告 / chunk 计数;复用现有 `Card` / `CardHeader` / `Stat`(或卡内小格子);无数据(空对象)时各计数显"—"。
- 文案中文,与现有"文档数/任务数"等 copy 一致。

## 6. 测试策略

**后端(`tests/`):**
1. `update_clean_state`:DB 有 chunk 但 `text_units.parquet` 缺失/旧 → 步后 parquet 含全部 chunk 行(断言行数 + id 集合)。
2. `write_text_units_parquet`(helper):full 与增量两路产物同形(同列、同行序无关的等值)。
3. `write_kb_stats`:用 FakeGraphAdapter 跑出一个有产物的 KB → 断言 `stats.json` 各计数正确;删一个 parquet → 该项为 0、不抛错。
4. orchestrator 集成:增量 job 成功 → `stats.json` 写了;full job 成功 → `stats.json` 也写了;`write_kb_stats` 抛错被吞、job 仍 SUCCEEDED。
5. 回归:既有增量测试全绿;新增 text_units 重建不破坏 `merge_delta` / embeddings。

**API(`tests/test_api_*.py`):**
6. `GET /kbs/{id}/stats`:有 stats.json → 返回内容;无 → 返回 `{}`。

**前端(`web/src/...`):**
7. `KbOverviewPage.test.tsx`:MSW mock `GET /kbs/{id}/stats` → 渲染"图谱规模"卡各计数;空对象 → 显"—"。

**E2E:** 跳过(stats 是快照,单测 + API 测已覆盖;真 LLM 冒烟可选,沿用既有 verify 流程)。

## 7. 非目标 / 延后项

- 实时 stats / DB 列 stats / 图收缩 / KB 列表页 stats —— 见 §2。
- 跨 KB 聚合统计(分析页已有独立 cost 聚合,不重叠)。
