# Phase 2a — 后端补全 设计文档

- 日期: 2026-06-24
- 状态: 已批准(待评审)
- 依赖: Phase 1 已合并(`main`,21 tests green);`graphrag==3.1.*`
- 上游设计: `docs/superpowers/specs/2026-06-24-kb-platform-design.md`(总体 spec,本文档细化其中 §6/§10 的 Phase 2 后端部分)

## 1. 背景与目标

Phase 1 只跑了一条最小流水线(`chunk_documents → extract_graph`),验证了"单元级追踪 + 单 chunk 重试"。Phase 2a 把索引补成**一条完整的图谱流水线**(抽取 → 描述合并 → 度数定稿 → Leiden 聚类 → 社区报告),仍以集成测试闭环验证,**不含向量化、查询、HTTP/UI**(那些分别在 Phase 3 / Phase 2b)。

核心交付:
1. 把 Phase 1 里 extract_graph 专用的"建单元 / 跑 / 落盘 / 合并结算"重构为**通用的 `UnitStepStrategy` 机制**,引擎(Orchestrator/UnitWorker)成为与 step 类型无关的驱动器。
2. 新增两个 unit 步(`summarize_descriptions`、`community_reports`)与两个 atomic 步(`finalize_graph`、`create_communities`)。
3. 实现**带失败前进**(`min_unit_success_ratio` 配置)。
4. 补齐 `Unit` 模型缺失列(spec §5)中 2a 真正用到的部分。

## 2. 范围

| 项 | 2a 是否含 |
|----|----------|
| 完整图谱流水线(extract→summarize→finalize→cluster→report) | ✅ |
| `UnitStepStrategy` 重构 + 通用 UnitWorker | ✅ |
| 带失败前进(`min_unit_success_ratio`) | ✅ |
| `Unit` 缺失列:`llm_raw_output`/`cost_json`/`input_hash`/`needs_reconsolidation` | ✅ |
| `Unit` 缺失列:`worker_id`/`heartbeat_at` | ❌ 延后 Phase 2b(需独立 worker 进程) |
| 向量化(`generate_text_embeddings`) | ❌ 随 Phase 3 查询一起做 |
| 查询(local/global/drift/basic) | ❌ Phase 3 |
| 反向引用(`create_final_documents`/`create_final_text_units`) | ❌ Phase 3 查询辅助 |
| REST API / React 仪表盘 / WebSocket | ❌ Phase 2b |
| 增量索引 / 重新整合动作 | ❌ Phase 3(2a 只**标记** `needs_reconsolidation`) |

## 3. 关键决策:`UnitStepStrategy` + 批量驱动通用 worker

Phase 1 的 `UnitWorker` 把 extract_graph 的逻辑硬编码。2a 抽成每个 unit 步一个 strategy,引擎只通用调度。

```python
class UnitStepStrategy(Protocol):
    kind: UnitKind
    def next_units_batch(self, repo, step) -> list[Subject] | None:
        """返回下一批'就绪'单元(批内独立并发);无更多返回 None。
        extract/summarize:首次返回全部 subject,再次 None。
        community_reports:按 level 自底向上,每次返回一层(下层报告已就绪才能建上层 context)。"""
    async def run_unit(self, adapter, unit) -> Any: ...
    def persist(self, data_root, unit, result) -> None: ...
    def finalize(self, repo, adapter, step, data_root, min_success_ratio: float) -> StepStatus: ...
```

`UnitWorker` 改为通用循环(不再认识具体 step):
```
strategy = STRATEGIES[step.name]
while (batch := strategy.next_units_batch(repo, step)) is not None:
    create + claim 该批 unit
    并发: result = await strategy.run_unit(adapter, unit)
           成功 → strategy.persist(...) + set_unit_succeeded(+ 记 cost/raw_output/input_hash)
           失败 → set_unit_failed
status = strategy.finalize(repo, adapter, step, data_root, min_success_ratio)
set_step_status(status)
```

**收益:** 开闭原则(Phase 3 加 embed = 新 strategy,引擎零改);批量模型天然表达 community_reports 的层级 DAG;Phase 1 extract_graph 顺势重构进 strategy(行为不变,有 Phase 1 测试兜底)。atomic 步(`finalize_graph`/`create_communities`)不走 strategy,留在 Orchestrator 的 atomic 分支(沿用 Phase 1 模式)。

## 4. 流水线与各 strategy 数据流

2a 步骤序列:
```
1 chunk_documents         atomic          [Phase 1]   → chunk 行
2 extract_graph           unit strategy   [重构]      → extractions/*.json → entities/relationships.parquet
3 summarize_descriptions  unit strategy   [新]        → summaries/*.json → 回写 entities.parquet(描述合并)
4 finalize_graph          atomic          [新]        → 算度数,回写 entities/relationships.parquet
5 create_communities      atomic          [新]        → Leiden → communities.parquet
6 community_reports       unit strategy   [新,多层]   → reports/*.json → community_reports.parquet
```
最终产出四张 parquet:entities(合并后描述)/ relationships / communities / community_reports。

**`extract_graph`(从 Phase 1 重构,行为不变)**
- `next_units_batch`:首次返回所有 chunk,再次 None。
- `run_unit`:`adapter.extract_chunk(chunk_id, text) -> ExtractionResult`。
- `persist`:`extractions/<chunk_id>.json`(entities+relationships)。
- `finalize`:load 全部成功单元 → `merge_extractions` → 写 `entities.parquet`+`relationships.parquet`。

**`summarize_descriptions`(新)**
- 依赖:`entities.parquet`(此时实体 `description` 为多 chunk 描述列表)。
- `next_units_batch`:subject = **描述数 >1 的实体**(单描述实体无需 unit,finalize 原样保留)。
- `run_unit`:`adapter.summarize_entity(name, descriptions: list[str]) -> str`。
- `persist`:`summaries/<entity>.json` = 合并后描述。
- `finalize`:load 全部 summaries → 把 entities.parquet 的 `description` 列从"列表"替换为"合并后字符串"(未建 unit 的实体保留原值)→ 回写 entities.parquet。

**`finalize_graph`(atomic,新)**:读 entities/relationships parquet → 算每实体 degree、关系 combined_degree → 回写。

**`create_communities`(atomic,新)**:读 relationships.parquet → graphrag `hierarchical_leiden` → 写 `communities.parquet`(`level, community_id, parent, entity_ids`)。

**`community_reports`(新,多层)**
- 依赖:entities + relationships + communities parquet。
- `next_units_batch`:**按 level 自底向上**——先返回最深层(叶子)社区;该层 unit 全完成后,下次返回上一层(此时 strategy 用已落盘的子社区报告构建上层 context);… 直到 None。
- `run_unit`:`adapter.report_community(community_context: dict) -> CommunityReport`。context = 该社区实体/关系 + 子社区报告摘要。
- `persist`:`reports/<community_id>.json`。
- `finalize`:load 全部 reports → 写 `community_reports.parquet`(`title, summary, findings, rank, full_content, level, community`)。

## 5. 带失败前进

单个 per-kb 配置 `min_unit_success_ratio`(默认 `1.0`=严格,等价 Phase 1)。strategy `finalize`:
```
success_ratio = succeeded / total
if success_ratio >= min_unit_success_ratio:
    用"成功单元"产物写最终 parquet → step SUCCEEDED(job 继续)
    失败单元保持 FAILED(可重试);step 结算后才重试成功的 → 标 needs_reconsolidation
else:
    不写 parquet → step PARTIALLY_FAILED(job 阻塞)
```
默认 `1.0` 完全保留 Phase 1 行为;`<1.0` 即"容忍少量 chunk 失败、带着缺口前进"。`needs_reconsolidation` 的重新整合动作留 Phase 3,2a 仅标记。

## 6. `Unit` 模型缺失列迁移(Alembic)

按"2a 是否真用上"取舍(YAGNI):
| 列 | 2a | 用途 |
|----|----|------|
| `llm_raw_output` (Text, nullable) | ✅ | 可观测性 |
| `cost_json` (Text, nullable) | ✅ | 成本追踪(adapter 从 LLM usage 填) |
| `input_hash` (String, nullable) | ✅ | 去重/缓存命中判定 |
| `needs_reconsolidation` (Bool, default false) | ✅ | 带失败前进下晚到单元标记 |
| `worker_id`、`heartbeat_at` | ❌ Phase 2b | 崩溃恢复需独立 worker 进程;2a 进程内单 worker 无用 |

迁移用 Alembic autogenerate;`test_migration.py` 断言新列存在。`UnitWorker` 在 `set_unit_succeeded` 时一并写入 `input_hash`/`cost_json`/`llm_raw_output`(由 `run_unit` 的结果携带)。

## 7. graphrag Adapter 接缝扩展

`GraphAdapter` Protocol 新增(Phase 1 的 `extract_chunk`/`merge_extractions` 保留):
```python
async summarize_entity(self, name: str, descriptions: list[str]) -> str
async report_community(self, community_context: dict) -> CommunityReport   # dataclass: title/summary/findings/rank/full_content/level/community
def cluster_relationships(self, relationships_df) -> pd.DataFrame          # atomic
def finalize_entities_relationships(self, entities_df, rels_df) -> tuple[pd.DataFrame, pd.DataFrame]  # atomic, 度数
```
`FakeGraphAdapter` 给四个原语确定性实现(测试底座);`GraphRagAdapter` 接 graphrag 的 `summarize_descriptions`、社区报告 extractor、`cluster_graph`(hierarchical_leiden)、finalize 操作。`graphrag_adapter.py` 仍是唯一 import graphrag 内部的模块。

## 8. 测试策略

- **每 strategy 单测**(FakeGraphAdapter):`next_units_batch`/`run_unit`/`persist`/`finalize`。
  - extract_graph strategy 重构后 = Phase 1 行为回归(既有测试兜底)。
  - summarize:多描述实体 → 合并建 unit;单描述实体 → 不建 unit、原值保留。
  - community_reports:**多层**——断言叶子层先处理、父层 context 含子层报告、parquet 写出。
- **atomic 步单测**:finalize_graph 度数、create_communities Leiden 产出。
- **编排集成测试**:完整 2a 流水线 → 四张 parquet 全产出 + 所有 unit 追踪 + job SUCCEEDED。
- **带失败前进测试**:`min_ratio=0.5` + 注入失败 → step SUCCEEDED 推进、parquet 仅含成功单元;失败 unit 重试 → 恢复;晚到成功 → `needs_reconsolidation=true`。
- **迁移测试**:upgrade 后新列存在。
- **Adapter 契约测试**:`summarize_entity`+`report_community` 用 graphrag MockLLM(canned)零成本验证。

## 9. 非目标 / 延后项

- 向量化与查询(Phase 3)。
- REST API / 仪表盘 / WebSocket(Phase 2b)。
- 增量索引 / 重新整合动作(Phase 3)。
- `worker_id`/`heartbeat_at` 列与崩溃恢复(Phase 2b,随独立 worker 进程)。
- 反向引用步 `create_final_documents`/`create_final_text_units`(Phase 3 查询辅助)。
