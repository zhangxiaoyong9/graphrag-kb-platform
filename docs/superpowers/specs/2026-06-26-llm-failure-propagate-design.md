# LLM 请求失败不再降级为空 — 让失败真正落到 unit

> 状态：已 brainstorm、已确认；待写实现计划。
> 日期：2026-06-26。
> 范围：`graphrag-kb-platform` 后端索引路径（`graphrag_adapter.py` 一处构造点）。

## 1. 背景

实测：用错误 key 索引时，`extract_graph` 的 unit 仍标「成功」——LLM 鉴权错误被吞成空结果。后果：失败被隐藏成「成功 + 空图谱」，下游拿到空图（如 `create_communities` 抛 `EmptyNetworkError`），且重试机制没有真正的 failed unit 可重试。

**根因（已核实 graphrag 源码）：** graphrag 的三个抽取器 `GraphExtractor` / `SummarizeExtractor` / `CommunityReportsExtractor` 各自的 `__call__` 把 LLM 调用 `_process_document`（即 `completion_async`）包在 `try/except Exception` 里：捕获后 `logger.exception` + 调 `self._on_error(e, trace, data)` + **返回空**（`_empty_entities_df()` 等）。`on_error: ErrorHandlerFn | None` 默认是 no-op（`lambda _e,_s,_d: None`）。平台在 `build_default_adapter`（`kb_platform/graph/graphrag_adapter.py`）里构造这三个抽取器时**没传 `on_error`** → 错误被吞。

关键边界：该 `try/except` **只包 LLM 调用**（`_process_document`），`_process_result`（解析）在 try 之外——解析错误本就传播。所以「降级为空」专指 **LLM 请求失败**，正是本次要修的。

`ErrorHandlerFn` 签名（`graphrag/index/operations/typing/error_handler.py`）：`Callable[[BaseException | None, str | None, dict | None], None]`。

## 2. 目标 / 非目标

**目标**
- LLM 请求失败（鉴权、网络、限流耗尽后）**传播为异常** → 平台 `run_unit` 捕获 → unit 标 `FAILED` 并存 `error`，可在 UnitTable 看到 + 可重试。
- 覆盖三个 LLM 步骤：extract_graph、summarize_descriptions、community_reports（结构化路径）。

**非目标**
- 不改解析失败的降级（`_parse_report_json` 仍对「成功响应但内容解析失败」做容错——那不是 LLM 请求失败）。
- 不改 plain community reports 路径（`report_community_plain` 已经在 `completion_async` 失败时抛错，本就传播）。
- 不改 graphrag-llm 的重试/限流（transient 错误仍由其 middleware 先重试；`on_error` 只在重试耗尽后触发）。
- 不改前端。

## 3. 设计

### 3.1 重新抛出的 on_error

`kb_platform/graph/graphrag_adapter.py` 模块级新增：
```python
def _raise_on_error(err: BaseException | None, _trace: str | None, _data: dict | None) -> None:
    """graphrag extractor on_error hook: re-raise so the platform's run_unit
    catches the LLM failure and marks the unit FAILED (instead of graphrag
    silently returning empty + the unit succeeding)."""
    if err is not None:
        raise err
```

### 3.2 构造三个抽取器时传入

`build_default_adapter` 里现有三处（约 258-275 行）：
```python
GraphExtractor(model=completion, prompt=GRAPH_EXTRACTION_PROMPT, max_gleanings=max_gleanings)
SummarizeExtractor(model=completion, max_summary_length=500, max_input_tokens=32000, summarization_prompt=SUMMARIZE_PROMPT)
CommunityReportsExtractor(model=completion, extraction_prompt=COMMUNITY_REPORT_PROMPT, max_report_length=2000)
```
各自加 `on_error=_raise_on_error`。delta 策略（extract/summarize/report 的 delta 变体）复用同一 factory，自动继承。

### 3.3 行为变化（预期）

- LLM 调用失败 → 抽取器 `__call__` 的 `except` 调 `_raise_on_error` → 抛出 → 平台 `ExtractGraphStrategy.run_unit`（等）不再 try/except（现状即如此）→ 异常上抛到 `UnitWorker._process` → unit 标 `FAILED`，`error` 字段写入异常信息。
- 原本「坏 key → 空图谱 → 下游 EmptyNetworkError」变成「坏 key → extract_graph unit FAILED（带鉴权错误）」，失败定位到具体 unit，可重试、可在 UI 看到。
- `min_unit_success_ratio` 仍决定步骤是否按 proceed-on-failure 继续；失败比例过高 → 步骤 `partially_failed`。
- transient 错误仍由 graphrag-llm 的重试/限流 middleware 处理；只有重试耗尽后才到 `on_error`。

## 4. 测试

- `tests/test_raise_on_error.py`（新建）：
  1. `test_raise_on_error_raises`：`_raise_on_error(RuntimeError("x"), None, None)` 抛 `RuntimeError`；`_raise_on_error(None, None, None)` 不抛。
  2. `test_graph_extractor_propagates_llm_failure`：构造一个 `_RaisingCompletion`（`completion_async` 直接抛），用 `GraphExtractor(model=raising, prompt="", max_gleanings=0, on_error=_raise_on_error)`，`asyncio.run(extractor(text="...", entity_types=[], source_id="c1"))` 应抛 `RuntimeError`（而非返回空 df）。证明 on_error 机制让失败传播。
- `test_build_default_adapter_embed.py` 既有 monkeypatch 模式可复用思路：可选补一个「`build_default_adapter` 构造的 extractor 带 `on_error`」的断言（通过把 `_raise_on_error` 设到 extractor 上后断言其 `_on_error is _raise_on_error`），但优先用上面的行为测试。
- 既有 186 后端测试全绿（无回归）。

## 5. 风险与对策

1. **transient 错误让 unit 误失败** — graphrag-llm 的重试/限流 middleware 先兜底；`on_error` 只在耗尽后触发。若实际发现 transient 误判，再调 graphrag-llm 重试参数。
2. **parse 失败被误传播** — 不会：graphrag 的 try/except 只包 LLM 调用，解析在 try 外。
3. **行为变化**：原本静默「成功」的坏配置任务现在会 fail——这正是目标；用户能在 UI/重试里看到真因。
4. **delta 策略** — 复用同一 factory，自动覆盖；无需单独改 delta.py。

## 6. 验收（Done）

- 错误 key 索引：`extract_graph` unit 标 `FAILED` 且 `error` 含鉴权信息（不再「成功 + 空」）。
- 现有 186 后端测试全绿；新增 2 个测试；ruff 干净。
- 既有「正确 key」索引行为不变（成功路径不受影响）。
