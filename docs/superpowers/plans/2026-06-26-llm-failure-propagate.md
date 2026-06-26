# LLM 失败不再降级为空 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 graphrag 抽取器的 LLM 请求失败（鉴权/网络/限流耗尽）传播为异常，落到 unit `FAILED`，而非被吞成空结果 + 假成功。

**Architecture:** graphrag 的三个抽取器（GraphExtractor / SummarizeExtractor / CommunityReportsExtractor）已暴露 `on_error: ErrorHandlerFn` 钩子（默认 no-op，吞异常返空）。在 `build_default_adapter` 里传一个「重新抛出」的 `on_error`，让失败从抽取器上抛 → 平台 `run_unit` 不再捕获 → `UnitWorker` 标 unit FAILED。

**Tech Stack:** Python 3.11 + uv + graphrag 3.1 + graphrag-llm + pytest/ruff。

## Global Constraints

- 后端 `uv run pytest` / `uv run ruff check .`；kb-platform **无 pyright/poe/semversioner**（ruff only）。
- 只改 `kb_platform/graph/graphrag_adapter.py`（+ 新测试文件）。
- `ErrorHandlerFn` 签型：`Callable[[BaseException | None, str | None, dict | None], None]`（`graphrag/index/operations/typing/error_handler.py`）。
- 不改解析失败的降级（`_parse_report_json` 仍容错）。
- 不改 plain community reports 路径（`report_community_plain` 已在 `completion_async` 失败时抛错）。
- 不改前端、不改 graphrag-llm 重试/限流 middleware。
- 异步测试用 `asyncio.run(...)`（沿用项目 `extract_chunk_sync` 模式，不引入 pytest-asyncio 依赖）。

---

## File Structure

- `kb_platform/graph/graphrag_adapter.py`（改）：新增模块级 `_raise_on_error`；`build_default_adapter` 内三处抽取器构造加 `on_error=_raise_on_error`。
- `tests/test_raise_on_error.py`（新建）：helper 行为 + 抽取器传播失败的行为测试。

---

## Task 1: `_raise_on_error` + 接入三个抽取器

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py`
- Test: `tests/test_raise_on_error.py`（新建）

**Interfaces:**
- Produces: 模块级 `_raise_on_error(err: BaseException | None, _trace: str | None, _data: dict | None) -> None`；`build_default_adapter` 构造的三个抽取器（`GraphExtractor`/`SummarizeExtractor`/`CommunityReportsExtractor`）均带 `on_error=_raise_on_error`。

- [ ] **Step 1: 写失败测试**

`tests/test_raise_on_error.py`：
```python
"""LLM request failures must propagate (mark unit FAILED), not degrade to empty.

graphrag's extractors swallow exceptions via their on_error hook (default no-op)
and return empty. We pass a re-raising on_error so the platform's run_unit
catches the failure and fails the unit.
"""
import asyncio

import pytest


def test_raise_on_error_raises_when_error_present():
    from kb_platform.graph.graphrag_adapter import _raise_on_error

    with pytest.raises(RuntimeError):
        _raise_on_error(RuntimeError("boom"), "trace", {"k": 1})


def test_raise_on_error_noop_when_no_error():
    from kb_platform.graph.graphrag_adapter import _raise_on_error

    # err is None -> must not raise (graphrag calls on_error unconditionally;
    # a None error would mean nothing went wrong)
    _raise_on_error(None, None, None)


class _RaisingCompletion:
    """Stand-in LLMCompletion whose completion_async always fails."""

    async def completion_async(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("auth failed")


def test_graph_extractor_propagates_llm_failure():
    from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor

    from kb_platform.graph.graphrag_adapter import _raise_on_error

    extractor = GraphExtractor(
        model=_RaisingCompletion(),
        prompt="",
        max_gleanings=0,
        on_error=_raise_on_error,
    )
    # An LLM failure must propagate (not return empty dataframes).
    with pytest.raises(RuntimeError, match="auth failed"):
        asyncio.run(extractor(text="some text", entity_types=[], source_id="c1"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv run pytest tests/test_raise_on_error.py -v`
Expected: FAIL — `ImportError: cannot import name '_raise_on_error'`（前两个测试）+ 第三个测试同因。

- [ ] **Step 3: 实现 `_raise_on_error` + 接入抽取器**

在 `kb_platform/graph/graphrag_adapter.py`：

(a) 模块级（`build_default_adapter` 定义**之前**，`_format_community_context` 等既有 helper 附近）新增：
```python
def _raise_on_error(err: BaseException | None, _trace: str | None, _data: dict | None) -> None:
    """graphrag extractor on_error hook: re-raise the LLM failure so the
    platform's run_unit catches it and marks the unit FAILED.

    graphrag's extractors default on_error to a no-op and return empty on any
    exception, which hides auth/network failures as fake successes. This makes
    them propagate. (Only the LLM call is wrapped by graphrag's try/except;
    parse errors already propagate.)
    """
    if err is not None:
        raise err
```

(b) 在 `build_default_adapter` 里把三个 factory 各加 `on_error=_raise_on_error`。把：
```python
    def extractor_factory() -> GraphExtractor:
        return GraphExtractor(
            model=completion, prompt=GRAPH_EXTRACTION_PROMPT, max_gleanings=max_gleanings
        )

    def summarize_factory() -> SummarizeExtractor:
        return SummarizeExtractor(
            model=completion,
            max_summary_length=500,
            max_input_tokens=32000,
            summarization_prompt=SUMMARIZE_PROMPT,
        )

    def report_factory() -> CommunityReportsExtractor:
        return CommunityReportsExtractor(
            model=completion,
            extraction_prompt=COMMUNITY_REPORT_PROMPT,
            max_report_length=2000,
        )
```
改为：
```python
    def extractor_factory() -> GraphExtractor:
        return GraphExtractor(
            model=completion,
            prompt=GRAPH_EXTRACTION_PROMPT,
            max_gleanings=max_gleanings,
            on_error=_raise_on_error,
        )

    def summarize_factory() -> SummarizeExtractor:
        return SummarizeExtractor(
            model=completion,
            max_summary_length=500,
            max_input_tokens=32000,
            summarization_prompt=SUMMARIZE_PROMPT,
            on_error=_raise_on_error,
        )

    def report_factory() -> CommunityReportsExtractor:
        return CommunityReportsExtractor(
            model=completion,
            extraction_prompt=COMMUNITY_REPORT_PROMPT,
            max_report_length=2000,
            on_error=_raise_on_error,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_raise_on_error.py -v`
Expected: PASS（3 个）。

- [ ] **Step 5: 全量回归 + ruff**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 既有 186 + 新增 3 全绿；ruff 干净。

- [ ] **Step 6: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/graph/graphrag_adapter.py tests/test_raise_on_error.py
git commit -m "fix(index): propagate LLM failures via on_error instead of degrading to empty"
```

---

## Self-Review

- **Spec 覆盖**：spec 3.1（`_raise_on_error` helper）→ Step 3(a)；3.2（三个抽取器加 `on_error`）→ Step 3(b)；3.3（行为变化）→ Step 1 的传播测试 + Step 5 回归；4（测试）→ Step 1 三测；5（风险：transient 由 graphrag-llm 兜底、parse 不受影响）→ 全局约束已声明不改解析/plain 路径。全覆盖。
- **占位符扫描**：无 TBD/TODO；每步含完整代码。
- **类型一致性**：`_raise_on_error(err, _trace, _data)` 签名在 helper 定义、测试、factory 调用三处一致（`ErrorHandlerFn` 签型）；delta 策略复用同一 factory，自动覆盖。
- **已知边界**：graphrag 的 try/except 只包 LLM 调用（`_process_document`），解析在 try 外——所以本改动只让 **LLM 请求失败**传播，解析失败行为不变（spec 非目标）。
