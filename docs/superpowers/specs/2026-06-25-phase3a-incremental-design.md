# Phase 3a — 增量索引 + 重新整合 设计文档

- 日期: 2026-06-25
- 状态: 已批准(待评审)
- 依赖: Phase 1 + 2a + 2b-1 + 2b-2 已合并(`main`,78 backend + 7 frontend tests)
- 上游设计: `docs/superpowers/specs/2026-06-24-kb-platform-design.md`(总体 spec §7 增量索引)

## 1. 背景与目标

这是用户最初 6 条诉求里**最后一条未实现**的:"增加新的文档时,可以在已有的图上建立关系,不要再重新对之前的文档进行解析了"。Phase 3a 实现**增量索引**:加新文档时只对新 chunk 跑 LLM,把新实体/关系并入老图(建立新↔老关系),旧文档零重算;并消费 `needs_reconsolidation`(2a 引入的迟到单元标记)。

不含 embeddings / 查询(Phase 3b)。

## 2. 范围

| 项 | 3a 是否含 |
|----|----------|
| `plan_incremental()` delta 步骤序列(独立于 full) | ✅ |
| 只对新 chunk 抽取(旧 chunk 文本零进 LLM) | ✅ |
| `merge_delta`(graphrag update 操作把 delta 并入主索引) | ✅ |
| delta 命名空间存储(`delta-<timestamp>/`) + 生命周期 | ✅ |
| 重新整合(消费 `needs_reconsolidation`,增量 job 收尾自动跑) | ✅ |
| `POST /kbs/{id}/jobs` 接受 `type=incremental` + worker 按类型选 plan | ✅ |
| embeddings / 向量库 | ❌ Phase 3b |
| 查询(local/global/drift/basic) | ❌ Phase 3b |
| 文档删除导致的图收缩 | ❌(后续可加,复用增量框架反向) |

## 3. 架构:独立 `plan_incremental()` + delta 子索引 + graphrag merge

full 管道(2a/2b 已稳)**零改动**;增量是平行的新代码路径,镜像 graphrag 自己的 Standard vs StandardUpdate。

```
Phase A — 在 delta 命名空间建"新文档的完整子索引"(复用现有 standard 策略,只喂新文档):
  load_update_documents(只新文档) → 切块 → extract_graph → summarize → finalize → cluster → community_reports
  写进 data_root/delta-<timestamp>/ 的独立 parquet
Phase B — merge_delta:把 delta 子索引并入主索引(graphrag update 操作):
  merge_delta(atomic) → update_clean_state(atomic)
```

**为什么"先建 delta 子索引再 merge"(而非交错):**
- 复用现有策略:`extract_graph`/`summarize`/`community_reports` 原样跑,只是数据源是新文档、输出到 delta 命名空间 —— 不写"delta 感知"分支(除下述一点),2a 策略零改动。
- merge 用 graphrag 成熟逻辑:跨索引实体解析、重聚类、受影响社区报告更新都由 graphrag `update_*` 处理,而非自研 delta 合并算法。
- 单元追踪天然适用:Phase A 的 LLM 步仍按 chunk/实体/社区 unit 化(只对 delta 数据);Phase B 的 merge 是原子步骤。

## 4. delta 感知点 + IncrementalPlanner

**唯一 delta 感知改动:** 现有 `extract_graph` 策略的 `next_units_batch` 对增量 job 的全新 step 会返回**所有** chunk(含旧)= 重抽旧文档。修正:
- 增量 job 的 `create_base_text_units` 只切新文档,并把**新 chunk_id 集合**记到 `delta_manifest.json`(delta 命名空间)。
- 增量版 `extract_graph` 策略的 `next_units_batch` 只返回 manifest 内的 chunk。
- summarize/cluster/report 自然只处理 extract 产出的 delta 实体 —— 无需额外 delta 感知。

**IncrementalPlanner 职责(因此简化):**
- 判定哪些文档是"新"的(主索引 documents 表里没有的)→ 喂 Phase A。
- 管理 delta 命名空间(`delta-<timestamp>/`)。
- Phase A 完成后触发 Phase B merge。

**"不重解析旧文档"保证:** Phase A 只喂新文档 → 步骤 1/2/3 只碰新 chunk → 旧 chunk 文本零进 LLM。merge 把新实体并入老图(新↔老关系由 graphrag 实体解析建立)。

## 5. `merge_delta`(graphrag 接缝)+ delta 存储生命周期 + 重新整合

**`merge_delta`(atomic,唯一新增 graphrag 耦合点):**
- 读 delta 命名空间 parquet(entities/relationships/communities/community_reports)+ 主索引 parquet。
- 调 graphrag update 操作合并:`_group_and_resolve_entities`(按 title 解析实体、合并描述)、`_update_and_merge_relationships`(合并关系)、`update_communities`/`update_community_reports`(重聚类后更新受影响社区报告)。
- 写回主索引 parquet;清理 delta 命名空间。
- 具体 op 与签名在 plan 阶段 grep 核实(同 2b-1 真实 adapter 套路)。merge 是确定性操作(无 LLM),契约测试用小 fixture 直接验证合并结果。

**delta 存储生命周期:** `data_root/delta-<timestamp>/` 存 Phase A 子索引;merge_delta 成功后合并进主 `data_root/` parquet,然后删 delta 目录。沿用 graphrag 带时间戳 delta + previous 备份模式(可回滚)。

**重新整合(`needs_reconsolidation` 消费):**
- `needs_reconsolidation` 单元 = 在 step 已结算后才成功的单元,产物在磁盘但未并入最终 parquet。
- 重新整合:收集所有 `needs_reconsolidation` 单元 → 重跑受影响 step 的 finalize(从磁盘加载"全部成功单元"含迟到的 → 合并 → 写 parquet)→ 清除 flag。复用 2a 的 finalize-from-all-succeeded 逻辑,**不跑新 LLM**。
- 触发:增量 job 收尾时**自动跑一次**(无需单独端点)。

**graphrag 耦合边界:** merge_delta + update 操作集中在 `kb_platform/graph/graphrag_update.py`(与 `graphrag_adapter.py` 并列);引擎/策略不碰 graphrag 内部。

## 6. 触发(API / worker)

- `POST /kbs/{id}/jobs`:`JobCreate` 加 `type: "full" | "incremental"`(默认 `full`);`JobCreated` 响应不变。
- Job 行已有 `type` 字段(Phase 1 模型);worker 领取后按 `job.type` 选 `plan_full()` 或 `plan_incremental()`。
- Orchestrator.run 读 `job.type` 选 plan;策略注册表复用(extract/summarize/community_reports 不变,增量版 extract_graph 过滤新 chunk)。
- 增量 job 收尾自动跑重新整合。

## 7. 测试策略

- **FakeGraphAdapter 增量测试**:建 KB + 索引文档集 A(full)→ 加文档集 B(增量)→ 断言 `extract_graph` 的 unit.subject **只含 B 的新 chunk_id**(直接验证"不重解析旧文档"核心承诺);merge 后 entities/relationships 含 A+B 且新↔老关系建立。
- **重新整合测试**:制造 `needs_reconsolidation` 单元 → 跑增量(含自动 reconsolidate)→ flag 清除、迟到数据并入 parquet。
- **merge_delta 契约测试**:小 fixture(主 + delta parquet)→ 调 merge → 断言实体按 title 解析合并、描述合并、关系合并。
- **2a/2b 回归**:full 管道 + 既有 78 测试全绿(full 零改动)。
- 真实 graphrag update op 导入路径在 plan 阶段 grep 核实。

## 8. 非目标 / 延后项

- embeddings + 向量库 + 查询(Phase 3b)。
- 文档删除导致的图收缩(后续可加)。
- 跨索引细粒度一致性(被触碰老实体描述重合并、变化社区报告)的显式重跑 —— 交 graphrag `update_*` 语义处理(用成熟逻辑换实现简化)。
- 增量 job 的独立 reconsolidate 端点(自动跑已够)。
