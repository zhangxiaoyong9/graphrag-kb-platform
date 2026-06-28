# update_clean_state + KB 图谱规模 — 验证记录

- 日期: 2026-06-28
- 分支: feat/update-clean-state
- 计划: `.superpowers/sdd/task-5-brief.md`(对应 A2 增量步骤 + KB 概览图谱规模卡)
- Base (main): `2a5df31` → HEAD `1693345`

## 功能

两件事:

1. **A2 · `update_clean_state` 步骤** —— 增量计划(`plan_incremental`)在
   `generate_text_embeddings` **之前**新增一步,用 full 与增量共享的写出器
   `write_text_units_parquet` 重建 `<data_root>/text_units.parquet`,把增量新文档
   的 chunk 写回数据面(full 计划本身在 `_chunk_documents` 已写,故 full 不受影响)。
   修复了"增量只追加 entities/relationships,新 chunk 不进 text_units.parquet"的缺口。
2. **KB 图谱规模快照** —— `orchestrator.run` 收尾处(`reconsolidate` 之后,full/增量都跑)
   调 `write_kb_stats` 写 `<data_root>/stats.json`(实体/关系/社区/文档/chunk 计数)。
   `GET /kbs/{id}/stats` 读它返回;stats.json 缺失返回全 `None` 空对象(200,非 404)。
   概览页"图谱规模"卡读该快照,未索引过的 KB 降级显"—"。

设计要点(实现已遵循):`write_text_units_parquet` 为 full/增量共享写出器(一处改两路同形);
`write_kb_stats` 调用点已包 try/except,内部每个 parquet 读取再容错,**两层都不抛**;
`GET /kbs/{id}/stats` 复用 `routes_export._data_root`,仅 KB 行缺失才 404。

## 环境

- macOS Darwin 25.3.0;Python 3.11(`uv` 虚拟环境);Node + Vite 5.4。
- 测试均在本机执行,无真实 LLM/Key;后端用 `FakeGraphAdapter` / 内存向量库,
  前端用 jsdom。`asyncio_mode = "auto"`。

## 自动化验证

### 后端全量

```
uv run pytest -q
```

结果:**242 passed, 1 warning in 11.80s**(1 条 httpx 弃用警告,`StarletteDeprecationWarning:
Using httpx with starlette.testclient`,预先存在、非本次引入)。Exit 0。

```
uv run ruff check .
```

结果:**All checks passed!**(无 unused-import 等)。Exit 0。

### 前端全量

```
cd web && npm test        # vitest run
```

结果:**Test Files 19 passed (19);Tests 71 passed (71);Duration 4.27s**。
日志含既有的 `UnitTable` "act(...)" 提醒(非失败)。Exit 0。

```
cd web && npm run build   # tsc -b && vite build
```

结果:**1114 modules transformed**;产物
`dist/index.js 477.50 kB (gzip 150.20 kB)`、`dist/index.css 35.32 kB`、
`dist/index.html 0.97 kB`;`built in 1.69s`。Exit 0。

## 行为映射 → 测试证据

Step 3(真实 server+worker 的浏览器冒烟)按任务说明留待用户人工执行;此处以全量自动化
测试证据覆盖两条 A2 可观测交付物:

### A2-1 · 增量 job 完成后 `text_units.parquet` 含新文档 chunk

`tests/test_incremental_pipeline.py::test_full_then_incremental_only_llms_new_chunks`
(全量回归在册):

```python
# A2: update_clean_state rebuilt text_units.parquet to include doc B's new
# # chunks (the incremental gap fix), and stats.json was written at job end.
tu = pd.read_parquet(f"{data_root}/text_units.parquet")
assert incr_chunk_ids.issubset(set(tu["id"])), "new chunks missing from text_units.parquet"
```

`incr_chunk_ids` 取自增量 extract step 的 units(`{u.subject_id for u in repo.list_units(incr_extract.id)}`),
即增量新 chunk id 集合;断言它们全部出现在 `text_units.parquet` 的 `id` 列 → 证明
`update_clean_state` 在 `generate_text_embeddings` 之前把新 chunk 落进了 text_units.parquet。

### A2-2 · `<data_root>/stats.json` 存在且计数合理

同一测试紧接断言:

```python
stats = json.loads(Path(data_root, "stats.json").read_text())
assert stats["entity_count"] >= 1
```

写出的职责侧由 `tests/test_kb_stats.py` 覆盖:

- `test_write_kb_stats_counts_parquet_and_db_rows` —— parquet 与 DB 行均计入,
  断言 `stats["entity_count"] == 3`(等)。
- `test_write_kb_stats_missing_parquet_is_zero_and_never_raises` —— 任一 parquet
  缺失则该项计 0,**整体不抛**(best-effort 的两层容错契约)。
- `test_write_kb_stats_unknown_kb_is_noop` —— 未知 KB 行直接 no-op。

读取侧由 `tests/test_api_kbs.py` 覆盖:

- `test_get_kb_stats_returns_snapshot` —— 预置 `stats.json`,`GET /kbs/{id}/stats`
  返回其内容。
- `test_get_kb_stats_empty_when_no_snapshot` —— 无 stats.json → 200 全 `None`
  空对象(UI 据此显"—"),**非 404**。

### 概览页"图谱规模"卡 + 未索引 KB 显"—"

`web/src/pages/KbOverviewPage.test.tsx`(vitest,在 71 之列):

- 有快照分支:预置 `entity_count: 9 / relationship_count: 7 / ...`,断言
  `await screen.findByText("图谱规模")` 出现且 `screen.getByText("9")` 命中 entity_count。
- 无快照分支:`waitFor` 后断言 `screen.getAllByText("—").length` 大于 0
  → 未索引过的 KB 降级显"—"成立。

## 结论

后端 242 passed / ruff clean、前端 71 passed / build clean —— 全绿,无跨任务回归。
两条 A2 交付物(text_units.parquet 重建覆盖增量新 chunk;stats.json 在 job 收尾写出 +
概览页图谱规模卡 + 未索引 KB 显"—")均由上述单元/集成测试断言覆盖。

真实 server+worker 的浏览器冒烟(Step 3,可选)按任务说明留给用户:跑一次
full → 加文档 → incremental,确认 `<data_root>/text_units.parquet` 含新 chunk、
`stats.json` 计数合理、概览页"图谱规模"卡显示计数、未索引 KB 显"—"。
