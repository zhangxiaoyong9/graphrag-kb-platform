# Phase 3b — Embeddings + 查询 设计文档

- 日期: 2026-06-25
- 状态: 已批准(待评审)
- 依赖: Phase 1 + 2a + 2b-1 + 2b-2 + 3a 已合并(`main`,90 tests)
- 上游设计: `docs/superpowers/specs/2026-06-24-kb-platform-design.md`(总体 spec §6 查询)

## 1. 背景与目标

3a 闭环了全部原始诉求。Phase 3b 是价值增量:**让平台能问答** —— 索引产出向量(embeddings),四种 search 方法(local/global/drift/basic)通过同步 API 返回答案。这是 GraphRAG 的招牌能力(RAG over knowledge graph)。

## 2. 范围

| 项 | 3b 是否含 |
|----|----------|
| `generate_text_embeddings` 步(三集合批量嵌入) | ✅ |
| VectorStore 接缝(Fake + LanceDB) | ✅ |
| 四种 QueryEngine(local/global/drift/basic)包 graphrag | ✅ |
| `POST /kbs/{id}/query` 同步 API | ✅ |
| 前端查询框(method + 输入 → 答案) | ✅ |
| embeddings 步加入 full + incremental 计划 | ✅ |
| global/drift 的 community_reports provider(配置项) | ✅ 文档说明 |
| 流式查询(streaming) | ❌(后续) |
| 异步查询 job | ❌(同步够用) |
| embeddings unit 化(每 item 追踪) | ❌ MVP 用 atomic |

## 3. 架构

```
索引侧(写入):
  generate_text_embeddings(atomic, full + incremental 末尾)→ VectorStore
     · text_units.text → text_unit 向量索引
     · entities.title+description → entity 向量索引
     · community_reports.full_content → community 向量索引

查询侧(读取):
  POST /kbs/{id}/query {method, query}
     · QueryEngine 加载索引 parquet + 向量库 → 按 method 构造 graphrag 引擎 → 跑 → 返回答案
     · local/basic:任何 provider;global/drift:需 community_reports(空 → 优雅返回)
```

## 4. VectorStore 接缝

`kb_platform/graph/vector_store.py`:
```python
class VectorStore(Protocol):
    def connect(self) -> None: ...
    def upsert(self, index_name: str, items: list[dict]) -> None: ...   # {id, text, vector}
    def query(self, index_name: str, text: str, k: int) -> list[dict]: ...  # {id, score}
```
- **FakeVectorStore**:内存 dict,确定性(query 返回前 k 个 by 插入顺序)。测试底座。
- **LanceDBVectorStore**:包 graphrag-vectors 的 LanceDB(本地文件 `data_root/vectors/`),真实存储。
- `adapter.embed_items(texts: list[str]) -> list[list[float]]`:接 graphrag-llm 的 `create_embedding`。

## 5. generate_text_embeddings 步(atomic)

- 加入 `plan_full()` 和 `plan_incremental()` 末尾(第 7 步)。
- 三个集合批量嵌入:读 parquet → 取嵌入文本 → `adapter.embed_items` → upsert 进对应 VectorStore 索引。
- atomic(失败重跑整步;廉价批量,不需 unit 追踪)。
- orchestrator `_run_atomic` 路由 `generate_text_embeddings`。

## 6. QueryEngine 接缝 + 四种搜索

`kb_platform/query/engine.py`:
```python
class QueryEngine(Protocol):
    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult: ...
```
- **FakeQueryEngine**:确定性返回(method + query 回显)。API/前端测试。
- **GraphRagQueryEngine**:唯一查询侧 graphrag 耦合。加载索引 parquet + LanceDB → 按 method 构造 graphrag 的 `LocalSearch`/`GlobalSearch`/`DRIFTSearch`/`BasicSearch` → 跑 → 返回答案。用 KB settings 的 LLM(`create_completion`)。

| method | 数据依赖 | DeepSeek |
|--------|---------|----------|
| local | entity 向量 + 实体/关系/chunk | ✅ |
| basic | text_unit 向量 | ✅ |
| global | community_reports | ⚠️ 需 json_schema 模型 |
| drift | local + global | ⚠️ 同 global |

global/drift 若 `community_reports.parquet` 为空 → 返回 `{"answer": "", "error": "no community reports; re-index with json_schema-capable model"}`。

## 7. 查询 API + 前端

**API:**
```
POST /kbs/{id}/query   {method, query}
  → 200 {answer, method}   (同步)
```
- Pydantic: `QueryRequest(method: str, query: str)`、`QueryResult(answer: str, method: str)`。
- 同步跑(用户等几秒);QueryEngine 可注入(Fake 测试 / GraphRag 生产)。

**前端:** KB 详情页加查询区:method 下拉 + 文本框 + "Ask" → 显示答案。`client.ts`: `query(kbId, method, query)`。

## 8. 测试策略

- **embeddings 步**:FakeVectorStore + FakeGraphAdapter(确定性向量)→ 跑步 → 断言三个索引各有向量、数量 = parquet 行数。
- **VectorStore**:Fake upsert/query 往返;LanceDB 小 fixture 写入→查回。
- **QueryEngine/API**:FakeQueryEngine → `POST /query` 返回 canned;method 透传。
- **前端**:RTL + msw → method + 输入 + Ask → 显示答案。
- **3a 回归**:full + incremental + 既有 90 测试全绿。
- **真实查询**:GraphRagQueryEngine 真实链路留手动冒烟(global 需 json_schema 模型的 reports)。

## 9. 非目标 / 延后项

- 流式查询 / 异步查询 job。
- embeddings unit 化(每 item 追踪 + 重试)—— atomic 足够。
- delta-aware embeddings(增量只嵌新项)—— 整步重嵌可接受。
- 查询结果引用/溯源展示。
- 查询缓存。
