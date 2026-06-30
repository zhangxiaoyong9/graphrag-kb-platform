# Markdown Structure-Aware Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in structure-aware chunker that never cuts inside a minimal unit (sentence / table row), selectable per-KB via `chunking.strategy`, defaulting new dashboard-created KBs to `"markdown"`.

**Architecture:** A new pure-Python `MarkdownChunker` (zero graphrag imports; tokenizer `encode`/`decode` injected) segments markdown into structural blocks, packs whole blocks up to `chunk_size` tokens, and only decomposes a block when it alone exceeds the budget (paragraph → sentences, table → rows with header repeated, list → items). `build_default_adapter` picks it when `chunk_strategy == "markdown"`. The frontend KB form exposes the choice; backend default stays `"tokens"` so existing/API KBs are unchanged.

**Tech Stack:** Python 3.11 / pytest / ruff (backend); React + TypeScript + Vite + vitest (frontend); graphrag's injected tiktoken tokenizer.

## Global Constraints

- Backend: `uv run pytest` (`asyncio_mode = "auto"`, `pythonpath` includes `tests`), `uv run ruff check .` (line-length 100, target py311).
- `chunk_id = sha512(text)` is unchanged — the new chunker returns objects with `.text`, and `GraphRagAdapter.chunk_document` already does `ChunkText(chunk_id=_hash(tc.text), text=tc.text)`. No adapter change.
- `kb_platform/graph/graphrag_adapter.py` is the ONLY module that imports graphrag internals. The new `markdown_chunker.py` imports stdlib only (`re`, `dataclasses`, `typing`); the tokenizer is injected.
- UI copy is Chinese — match surrounding copy. Use the existing `Field` component and `select`/`input`/`text-muted` classes (`web/src/components/ui.tsx`).
- Frontend tests: `cd web && npm test -- <file>` (vitest run). Backend tests: `uv run pytest <file> -v`.
- End commit messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: MarkdownChunker — segmentation, packing, heading attachment

**Files:**
- Create: `kb_platform/graph/markdown_chunker.py`
- Test: `tests/test_markdown_chunker.py`

**Interfaces:**
- Produces: `MarkdownChunker(size=int, encode=Callable[[str], list[int]], decode=Callable[[list[int]], str] | None).chunk(text: str) -> list[MarkdownChunk]`; `MarkdownChunk` has `.text: str` and `.index: int`. Also module-level `segment(text) -> list[_Block]` and `split_sentences(text) -> list[str]`.

- [ ] **Step 1: Write the failing tests (happy path — fitting blocks, headings, plain text)**

Create `tests/test_markdown_chunker.py`:

```python
"""MarkdownChunker: structure-aware chunking that never cuts inside a minimal unit."""

from kb_platform.graph.markdown_chunker import MarkdownChunker, MarkdownChunk


def _enc(s: str) -> list[str]:
    return s.split()


def _dec(toks: list[str]) -> str:
    return " ".join(toks)


def _chunker(size: int) -> MarkdownChunker:
    return MarkdownChunker(size=size, encode=_enc, decode=_dec)


def test_empty_text_returns_nothing():
    assert _chunker(10).chunk("") == []
    assert _chunker(10).chunk("   \n\n  ") == []


def test_fitting_paragraphs_pack_into_one_chunk():
    text = "alpha beta gamma\n\ndelta epsilon zeta"
    chunks = _chunker(100).chunk(text)
    assert len(chunks) == 1
    assert "alpha beta gamma" in chunks[0].text
    assert "delta epsilon zeta" in chunks[0].text


def test_paragraph_not_split_mid_block():
    # each paragraph 6 tokens; size=8 -> they cannot share a chunk (6+1+6 > 8)
    text = "a b c d e f\n\ng h i j k l"
    chunks = _chunker(8).chunk(text)
    assert len(chunks) == 2
    assert chunks[0].text == "a b c d e f"
    assert chunks[1].text == "g h i j k l"


def test_fitting_table_kept_whole():
    table = "| col_a | col_b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    chunks = _chunker(100).chunk(table)
    assert len(chunks) == 1
    assert "| col_a | col_b |" in chunks[0].text
    assert "|---|---|" in chunks[0].text
    assert "| 3 | 4 |" in chunks[0].text


def test_fitting_list_kept_whole():
    text = "- one item\n- two item\n- three item"
    chunks = _chunker(100).chunk(text)
    assert len(chunks) == 1
    assert "- three item" in chunks[0].text


def test_fenced_code_with_blank_line_kept_whole():
    text = "```python\n\n\ndef f():\n    pass\n\n\n```"
    chunks = _chunker(100).chunk(text)
    assert len(chunks) == 1
    assert "```python" in chunks[0].text


def test_heading_attaches_to_following_paragraph():
    text = "# Title\n\nbody text here"
    chunks = _chunker(100).chunk(text)
    assert len(chunks) == 1
    assert "# Title" in chunks[0].text
    assert "body text here" in chunks[0].text


def test_heading_travels_across_chunk_boundary():
    para1 = " ".join(f"w{i}" for i in range(20))  # 20 tokens
    para2 = " ".join(f"x{i}" for i in range(20))  # 20 tokens
    text = f"# S1\n\n{para1}\n\n# S2\n\n{para2}"
    chunks = _chunker(25).chunk(text)
    # H1 travels with para1; H2 is NOT stranded — it joins para2
    assert any("# S1" in c.text and para1 in c.text for c in chunks)
    assert not any(c.text.strip() == "# S2" for c in chunks)
    h2_chunk = next(c for c in chunks if "# S2" in c.text)
    assert para2 in h2_chunk.text


def test_plain_text_without_structure_is_packed():
    text = "just a few words here nothing fancy"
    chunks = _chunker(100).chunk(text)
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_indices_are_sequential():
    chunks = _chunker(8).chunk("a b c d e f\n\ng h i j k l")
    assert [c.index for c in chunks] == [0, 1]


def test_chunk_return_type_has_text_field():
    chunks = _chunker(100).chunk("hello world")
    assert isinstance(chunks[0], MarkdownChunk)
    assert isinstance(chunks[0].text, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_markdown_chunker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kb_platform.graph.markdown_chunker'`

- [ ] **Step 3: Write minimal implementation**

Create `kb_platform/graph/markdown_chunker.py`:

```python
"""Markdown structure-aware chunker.

Never cuts inside a minimal unit (sentence / table row): packs whole markdown
blocks (headings, paragraphs, tables, lists, code fences) up to ``size`` tokens.
A block that alone exceeds ``size`` is emitted whole rather than bisected.

Zero graphrag imports: the tokenizer's ``encode``/``decode`` are injected,
mirroring graphrag's TokenChunker wiring (graphrag_adapter.build_default_adapter).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_HEADING_RE = re.compile(r"^#{1,6}\s")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_LIST_RE = re.compile(r"^\s*([-*+]|\d+\.)\s")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# Sentence terminators (Chinese + Western); zero-width lookbehind so the
# terminator stays with its sentence.
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？.!?;；])")


@dataclass
class MarkdownChunk:
    text: str
    index: int = 0


@dataclass
class _Block:
    type: str  # heading | paragraph | table | list | code
    text: str


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences (Chinese + Western), no model deps.

    Keeps the terminator with its sentence; drops empties.
    """
    parts = _SENT_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _classify(blob: str) -> _Block:
    lines = [ln for ln in blob.splitlines() if ln.strip()]
    first = lines[0]
    if _HEADING_RE.match(first) and len(lines) == 1:
        return _Block("heading", blob.strip())
    if all(_TABLE_ROW_RE.match(ln) for ln in lines):
        return _Block("table", blob.strip())
    if all(_LIST_RE.match(ln) for ln in lines):
        return _Block("list", blob.strip())
    return _Block("paragraph", blob.strip())


def segment(text: str) -> list[_Block]:
    """Split markdown into ordered structural blocks.

    Blank lines delimit blocks; fenced code (```/~~~) is kept whole, including
    any blank lines inside the fence.
    """
    blocks: list[_Block] = []
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if _CODE_FENCE_RE.match(line):
            start = i
            i += 1
            while i < n and not _CODE_FENCE_RE.match(lines[i]):
                i += 1
            i += 1  # include the closing fence (or run to EOF)
            blocks.append(_Block("code", "\n".join(lines[start:i])))
            continue
        if not line.strip():
            i += 1
            continue
        start = i
        while i < n and lines[i].strip():
            i += 1
        blocks.append(_classify("\n".join(lines[start:i])))
    return blocks


class MarkdownChunker:
    def __init__(
        self,
        *,
        size: int,
        encode: Callable[[str], list[int]],
        decode: Callable[[list[int]], str] | None = None,
    ) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        self._size = size
        self._encode = encode
        self._decode = decode

    def _tok(self, s: str) -> int:
        return len(self._encode(s))

    def chunk(self, text: str) -> list[MarkdownChunk]:
        if not text or not text.strip():
            return []
        out: list[str] = []
        current = ""
        pending: str | None = None  # buffered heading(s) awaiting their body

        def flush() -> None:
            nonlocal current
            if current:
                out.append(current)
                current = ""

        for block in segment(text):
            if block.type == "heading":
                pending = block.text if pending is None else f"{pending}\n{block.text}"
                continue
            if self._tok(block.text) > self._size:
                flush()
                out.extend(self._split_oversized(block, prefix=pending or ""))
                pending = None
                continue
            head = pending
            pending = None
            body = f"{head}\n\n{block.text}" if head else block.text
            if current and self._tok(f"{current}\n\n{body}") > self._size:
                flush()
                current = body
            else:
                current = body if not current else f"{current}\n\n{body}"
        flush()
        if pending:  # doc ends with a heading and no following body
            out.append(pending)
        return [MarkdownChunk(text=t, index=i) for i, t in enumerate(out)]

    def _split_oversized(self, block: _Block, *, prefix: str = "") -> list[str]:
        """Emit a block that alone exceeds the budget.

        The block is emitted whole (any buffered heading is prefixed to it) so no
        content is lost or invented and no unit is bisected.
        """
        pieces = [block.text]
        if prefix and pieces:
            pieces[0] = f"{prefix}\n\n{pieces[0]}"
        return pieces
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_markdown_chunker.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/graph/markdown_chunker.py tests/test_markdown_chunker.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/graph/markdown_chunker.py tests/test_markdown_chunker.py
git commit -m "feat(chunk): structure-aware markdown chunker — segmentation + packing

Segments markdown into structural blocks (heading/paragraph/table/list/code),
packs whole blocks up to chunk_size tokens, and attaches a heading to the
block that follows it so no chunk strands a bare heading. Zero graphrag
imports; tokenizer encode/decode injected. Oversized-block decomposition
follows in the next commit.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: MarkdownChunker — oversized-block decomposition

**Files:**
- Modify: `kb_platform/graph/markdown_chunker.py` (replace `_split_oversized`; add `_split_prose`, `_split_table`, `_split_by_items`, `_token_split`)
- Test: `tests/test_markdown_chunker.py` (append cases)

**Interfaces:**
- Consumes: `MarkdownChunker` from Task 1 (same constructor; `.chunk()` calls `_split_oversized`).
- Produces: unchanged public surface (`MarkdownChunker.chunk`); `_split_oversized` now decomposes instead of emitting whole.

- [ ] **Step 1: Write the failing tests (oversized prose / table / list / token fallback)**

Append to `tests/test_markdown_chunker.py`:

```python
def test_oversized_paragraph_splits_at_sentence_boundaries():
    # 3 sentences, each 4 word-tokens; size=5 -> none can pair (4+1+4 > 5)
    text = "one two three four. five six seven eight. nine ten eleven twelve."
    chunks = _chunker(5).chunk(text)
    assert [c.text for c in chunks] == [
        "one two three four.",
        "five six seven eight.",
        "nine ten eleven twelve.",
    ]


def test_chinese_sentence_split():
    # encode = per-char: each sentence is 6 chars; size=6 -> one sentence per chunk
    chunker = MarkdownChunker(size=6, encode=list, decode=lambda t: "".join(t))
    chunks = chunker.chunk("第一句内容。第二句内容。第三句内容。")
    assert [c.text for c in chunks] == ["第一句内容。", "第二句内容。", "第三句内容。"]


def test_single_sentence_larger_than_size_token_splits():
    # one long sentence (no terminator), 12 word-tokens, size=5 -> token-split 5+5+2
    text = "a b c d e f g h i j k l"
    chunks = _chunker(5).chunk(text)
    sizes = [len(c.text.split()) for c in chunks]
    assert max(sizes) <= 5
    assert sizes == [5, 5, 2]


def test_oversized_table_splits_by_row_repeating_header():
    header = "| a | b | c |"
    sep = "|---|---|---|"
    rows = [f"| {i} | {i} | {i} |" for i in range(1, 7)]  # 6 body rows
    text = "\n".join([header, sep, *rows])
    chunks = _chunker(12).chunk(text)
    assert len(chunks) >= 2
    for c in chunks:  # every piece repeats the header + separator
        assert header in c.text
        assert sep in c.text
    all_rows = [r for c in chunks for r in rows if r in c.text]
    assert sorted(all_rows) == sorted(rows)  # no row lost or duplicated


def test_huge_table_row_emitted_alone_with_header():
    header = "| a | b |"
    sep = "|---|---|"
    huge = "| " + " ".join(f"c{i}" for i in range(20)) + " |"  # ~21 tokens
    text = "\n".join([header, sep, huge, "| d | e |"])
    chunks = _chunker(10).chunk(text)
    huge_chunks = [c for c in chunks if huge in c.text]
    assert len(huge_chunks) == 1  # not split mid-row
    assert header in huge_chunks[0].text
    assert any("| d | e |" in c.text for c in chunks)


def test_oversized_list_splits_by_item():
    items = [f"- item number {i} here" for i in range(5)]  # each ~5 tokens
    text = "\n".join(items)
    chunks = _chunker(8).chunk(text)
    assert len(chunks) >= 2
    for it in items:  # no item bisected
        assert sum(1 for c in chunks if it in c.text) == 1


def test_heading_prepended_to_first_oversized_piece():
    # paragraph oversized; the buffered heading prefixes the first emitted piece
    text = "# H\n\n" + " ".join(f"w{i}" for i in range(30))  # one 30-token sentence
    chunks = _chunker(8).chunk(text)
    assert chunks[0].text.startswith("# H")


def test_code_block_is_never_split():
    # code > size: emitted whole (fence integrity), even though it exceeds budget
    code = "```py\n" + "\n".join(f"line{i}" for i in range(40)) + "\n```"
    chunks = _chunker(5).chunk(code)
    assert len(chunks) == 1
    assert "```py" in chunks[0].text and chunks[0].text.endswith("```")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_markdown_chunker.py -v`
Expected: the 8 new tests FAIL (oversized blocks still emitted whole / wrong splits). Task-1 tests still pass.

- [ ] **Step 3: Replace `_split_oversized` and add the decomposition methods**

In `kb_platform/graph/markdown_chunker.py`, replace the `_split_oversized` method body with the dispatch + add four helper methods. The final methods block of `MarkdownChunker` becomes (replacing the placeholder `_split_oversized`):

```python
    def _split_oversized(self, block: _Block, *, prefix: str = "") -> list[str]:
        if block.type == "table":
            pieces = self._split_table(block.text)
        elif block.type == "list":
            pieces = self._split_by_items(block.text, sep="\n")
        elif block.type == "code":
            pieces = [block.text]  # never split a code fence
        else:  # paragraph / heading
            pieces = self._split_prose(block.text)
        if prefix and pieces:
            pieces[0] = f"{prefix}\n\n{pieces[0]}"
        return pieces

    def _split_prose(self, text: str) -> list[str]:
        units = split_sentences(text) or [text]
        return self._pack_units(units, text, sep=" ", oversized=self._token_split)

    def _split_by_items(self, text: str, *, sep: str = "\n") -> list[str]:
        units = text.splitlines() or [text]
        return self._pack_units(units, text, sep=sep, oversized=self._token_split)

    def _split_table(self, text: str) -> list[str]:
        lines = text.splitlines()
        header = [lines[0]]
        rest_start = 1
        if len(lines) > 1 and _TABLE_SEP_RE.match(lines[1]):
            header.append(lines[1])
            rest_start = 2
        header_str = "\n".join(header)
        body = lines[rest_start:]
        pieces: list[str] = []
        cur: list[str] = []
        for row in body:
            if self._tok(row) >= self._size:  # single row >= budget: alone, with header
                if cur:
                    pieces.append("\n".join([header_str, *cur]))
                    cur = []
                pieces.append("\n".join([header_str, row]))
                continue
            candidate = "\n".join([header_str, *cur, row])
            if cur and self._tok(candidate) > self._size:
                pieces.append("\n".join([header_str, *cur]))
                cur = [row]
            else:
                cur.append(row)
        if cur:
            pieces.append("\n".join([header_str, *cur]))
        return pieces or [text]

    def _pack_units(
        self,
        units: list[str],
        whole: str,
        *,
        sep: str,
        oversized: Callable[[str], list[str]],
    ) -> list[str]:
        """Greedily pack small units (sentences / list items) up to ``size``.

        A unit that alone exceeds ``size`` is delegated to ``oversized`` (token
        splitting) instead of being packed.
        """
        pieces: list[str] = []
        cur = ""
        for u in units:
            if self._tok(u) > self._size:
                if cur:
                    pieces.append(cur)
                    cur = ""
                pieces.extend(oversized(u))
                continue
            joined = f"{cur}{sep}{u}" if cur else u
            if self._tok(joined) > self._size:
                if cur:
                    pieces.append(cur)
                cur = u
            else:
                cur = joined
        if cur:
            pieces.append(cur)
        return pieces or [whole]

    def _token_split(self, sentence: str) -> list[str]:
        """Hard token-level split of a single unit that still exceeds ``size``."""
        if self._decode is None:
            return [sentence[i : i + self._size] for i in range(0, len(sentence), self._size)] or [
                sentence
            ]
        tokens = self._encode(sentence)
        if not tokens:
            return [sentence]
        return [
            self._decode(tokens[i : i + self._size])
            for i in range(0, len(tokens), self._size)
        ] or [sentence]
```

Also update the module docstring so it describes decomposition (accurate now). Replace:

```python
    Never cuts inside a minimal unit (sentence / table row): packs whole markdown
    blocks (headings, paragraphs, tables, lists, code fences) up to ``size`` tokens.
    A block that alone exceeds ``size`` is emitted whole rather than bisected.
```

with:

```python
    Never cuts inside a minimal unit (sentence / table row): packs whole markdown
    blocks (headings, paragraphs, tables, lists, code fences) up to ``size`` tokens;
    a block that alone exceeds ``size`` is decomposed (paragraph -> sentences, table
    -> rows with the header repeated, list -> items), and a single sentence still
    over budget falls back to a token split. Code blocks are never split (fence
    integrity).
```

The new `_split_oversized` shown below carries no docstring (its dispatch is self-evident); it replaces Task 1's whole-block version and its docstring entirely.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_markdown_chunker.py -v`
Expected: PASS (all 19 tests).

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/graph/markdown_chunker.py tests/test_markdown_chunker.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/graph/markdown_chunker.py tests/test_markdown_chunker.py
git commit -m "feat(chunk): decompose oversized blocks (sentence/row/item)

Paragraphs split at sentence boundaries (Chinese + Western), tables split by
row with the header repeated on every piece, lists split by item; a single
sentence still over budget falls back to a token split. Code blocks stay whole.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Backend wiring — `chunking.strategy` selects the chunker

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py` — `build_default_adapter` signature + chunker selection (~lines 296-344); `build_adapter_from_settings` call site (~lines 471-488)
- Test: `tests/test_build_adapter_settings.py` (append cases)

**Interfaces:**
- Consumes: `MarkdownChunker` from Task 1–2.
- Produces: `build_default_adapter(..., chunk_strategy: str = "tokens")` selects `MarkdownChunker` when `"markdown"`, else graphrag's `TokenChunker`. `build_adapter_from_settings` passes `chunk_strategy=chunking.get("strategy", "tokens")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_build_adapter_settings.py`:

```python
def test_build_default_adapter_markdown_wires_markdown_chunker(monkeypatch):
    import graphrag_chunking.chunker_factory as cf
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod
    from graphrag_llm.config import ModelConfig

    from kb_platform.graph.graphrag_adapter import build_default_adapter
    from kb_platform.graph.markdown_chunker import MarkdownChunker

    monkeypatch.setattr(comp_mod, "create_completion", lambda mc: object())
    monkeypatch.setattr(emb_mod, "create_embedding", lambda mc: object())
    monkeypatch.setattr(cf, "create_chunker", lambda *a, **k: object())

    adapter = build_default_adapter(
        data_root="/tmp/_x_",
        model_config=ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x"),
        chunk_strategy="markdown",
        chunk_size=500,
    )
    assert isinstance(adapter._chunker, MarkdownChunker)


def test_build_default_adapter_default_strategy_is_tokens(monkeypatch):
    import graphrag_chunking.chunker_factory as cf
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod
    from graphrag_llm.config import ModelConfig

    from kb_platform.graph.graphrag_adapter import build_default_adapter
    from kb_platform.graph.markdown_chunker import MarkdownChunker

    captured: dict = {}

    def fake_create_chunker(cfg, encode, decode):
        captured["cfg"] = cfg
        return object()

    monkeypatch.setattr(comp_mod, "create_completion", lambda mc: object())
    monkeypatch.setattr(emb_mod, "create_embedding", lambda mc: object())
    monkeypatch.setattr(cf, "create_chunker", fake_create_chunker)

    adapter = build_default_adapter(
        data_root="/tmp/_x_",
        model_config=ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x"),
        chunk_size=300,
    )
    assert not isinstance(adapter._chunker, MarkdownChunker)
    assert captured["cfg"].size == 300


def test_build_adapter_from_settings_passes_chunk_strategy(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga

    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    settings = {
        "llm": {"model": "x", "api_keys": ["k"]},
        "chunking": {"strategy": "markdown", "size": 700},
    }
    ga.build_adapter_from_settings(json.dumps(settings), "/tmp/_x_")
    assert captured["chunk_strategy"] == "markdown"
    assert captured["chunk_size"] == 700


def test_build_adapter_from_settings_default_strategy_is_tokens(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga

    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    ga.build_adapter_from_settings(
        json.dumps({"llm": {"model": "x", "api_keys": ["k"]}}), "/tmp/_x_"
    )
    assert captured["chunk_strategy"] == "tokens"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_build_adapter_settings.py -v`
Expected: the 4 new tests FAIL (`chunk_strategy` not accepted / not passed).

- [ ] **Step 3: Add `chunk_strategy` to `build_default_adapter` and select the chunker**

In `kb_platform/graph/graphrag_adapter.py`:

(a) Add the kwarg to the `build_default_adapter` signature. After the `encoding_model: str = "cl100k_base",` line add:

```python
    chunk_strategy: str = "tokens",
```

(b) Replace the chunker construction (the `tokenizer = get_tokenizer(...)` + `chunker = create_chunker(...)` block) with strategy-aware selection. Find:

```python
    tokenizer = get_tokenizer(encoding_model=encoding_model)
    chunker = create_chunker(
        ChunkingConfig(
            type=ChunkerType.Tokens,
            encoding_model=encoding_model,
            size=chunk_size,
            overlap=chunk_overlap,
        ),
        encode=tokenizer.encode,
        decode=tokenizer.decode,
    )
```

Replace with:

```python
    tokenizer = get_tokenizer(encoding_model=encoding_model)
    if chunk_strategy == "markdown":
        # Structure-aware: never cuts inside a sentence / table row. Zero graphrag
        # imports in the chunker module; tokenizer injected like TokenChunker.
        from kb_platform.graph.markdown_chunker import MarkdownChunker

        chunker = MarkdownChunker(
            size=chunk_size, encode=tokenizer.encode, decode=tokenizer.decode
        )
    else:
        chunker = create_chunker(
            ChunkingConfig(
                type=ChunkerType.Tokens,
                encoding_model=encoding_model,
                size=chunk_size,
                overlap=chunk_overlap,
            ),
            encode=tokenizer.encode,
            decode=tokenizer.decode,
        )
```

- [ ] **Step 4: Pass `chunk_strategy` from `build_adapter_from_settings`**

In the `build_default_adapter(...)` call inside `build_adapter_from_settings`, add the strategy kwarg. Find the `chunk_size=chunking.get("size", 1200),` line within that call and add immediately after it:

```python
        chunk_strategy=chunking.get("strategy", "tokens"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_adapter_settings.py -v`
Expected: PASS (all tests, including pre-existing ones).

- [ ] **Step 6: Run full backend test suite + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_build_adapter_settings.py
git commit -m "feat(graph): chunking.strategy selects markdown vs token chunker

build_default_adapter gains chunk_strategy (default 'tokens'); when 'markdown'
it wires MarkdownChunker instead of graphrag's TokenChunker.
build_adapter_from_settings reads chunking.strategy (default 'tokens' so
existing/API KBs are unchanged).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Frontend settings layer — `chunking.strategy` round-trip

**Files:**
- Modify: `web/src/lib/kb-settings.ts` — interface (line 10), `DEFAULTS` (line 35), `buildSettings` (after line 68), `parseSettings` (lines 185-189)
- Test: Create `web/src/lib/kb-settings.test.ts`

**Interfaces:**
- Produces: `KbFormState.chunking.strategy: string`; `DEFAULTS.chunking.strategy === "markdown"` (new-form default); `buildSettings` always serializes `chunking.strategy`; `parseSettings` reads it back, defaulting to `"tokens"` for KBs whose stored settings predate the key (matches the backend default).

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/kb-settings.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { DEFAULTS, buildSettings, parseSettings } from "./kb-settings";

describe("chunking strategy", () => {
  it("defaults a new form to markdown", () => {
    expect(DEFAULTS.chunking.strategy).toBe("markdown");
  });

  it("buildSettings force-writes strategy even when it equals the default", () => {
    const out = buildSettings({ ...DEFAULTS });
    expect((out.chunking as Record<string, unknown>).strategy).toBe("markdown");
  });

  it("buildSettings writes an explicit tokens strategy", () => {
    const out = buildSettings({ ...DEFAULTS, chunking: { ...DEFAULTS.chunking, strategy: "tokens" } });
    expect((out.chunking as Record<string, unknown>).strategy).toBe("tokens");
  });

  it("parseSettings defaults a pre-feature KB (no key) to tokens", () => {
    expect(parseSettings({}, "standard", "1.0").chunking.strategy).toBe("tokens");
  });

  it("parseSettings round-trips an explicit markdown strategy", () => {
    const s = parseSettings({ chunking: { strategy: "markdown" } }, "standard", "1.0");
    expect(s.chunking.strategy).toBe("markdown");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npm test -- src/lib/kb-settings.test.ts`
Expected: FAIL — `strategy` missing on `DEFAULTS.chunking` / `parseSettings` result.

- [ ] **Step 3: Add `strategy` to the state, defaults, serializer, and parser**

In `web/src/lib/kb-settings.ts`:

(a) Interface (line 10) — add `strategy`:

```typescript
  chunking: { size: number; overlap: number; encodingModel: string; strategy: string };
```

(b) `DEFAULTS` (line 35) — new-form default is markdown:

```typescript
  chunking: { size: 1200, overlap: 100, encodingModel: "cl100k_base", strategy: "markdown" },
```

(c) `buildSettings` — after the `encoding_model` `addIf` block (the block ending at line 68), insert a force-write of `strategy` (NOT `addIf`, so the chosen value is always persisted even when it equals the form default):

```typescript
  // strategy is always persisted (force-write, not addIf) so a new KB explicitly
  // stores markdown even though it's the form default. The BACKEND default stays
  // "tokens", so this explicit write is what makes new dashboard KBs use markdown.
  {
    const b = (out.chunking ?? {}) as Record<string, unknown>;
    b.strategy = state.chunking.strategy;
    out.chunking = b;
  }
```

(d) `parseSettings` (lines 185-189) — read it back with a `"tokens"` default (NOT `DEFAULTS.chunking.strategy`, so a pre-feature KB reflects what the backend will actually do):

```typescript
    chunking: {
      size: n(ch, "size", DEFAULTS.chunking.size),
      overlap: n(ch, "overlap", DEFAULTS.chunking.overlap),
      encodingModel: f(ch, "encoding_model", DEFAULTS.chunking.encodingModel),
      strategy: f(ch, "strategy", "tokens"),
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npm test -- src/lib/kb-settings.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the existing frontend test suite to confirm no regressions**

Run: `cd web && npm test`
Expected: PASS (all component + lib tests).

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/kb-settings.ts web/src/lib/kb-settings.test.ts
git commit -m "feat(web): chunking.strategy in KB settings model

New form defaults to markdown; buildSettings force-writes strategy; parseSettings
reads it back defaulting to tokens for KBs that predate the key (matches backend).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Frontend UI — chunking-strategy selector in the KB form

**Files:**
- Modify: `web/src/components/KbForm.tsx` — the `分块 Chunking` panel (~lines 226-265)
- Test: `web/src/components/KbForm.test.tsx` (append cases)

**Interfaces:**
- Consumes: `KbFormState.chunking.strategy` from Task 4.
- Produces: a `切片方式` `<select>` (markdown / tokens) in the chunking panel; `overlap` input disabled when strategy is not `tokens`.

- [ ] **Step 1: Write the failing tests**

Append to `web/src/components/KbForm.test.tsx` (inside the existing `describe`/at top level alongside the other `test(...)` blocks, before the `// --- edit mode ---` section):

```typescript
test("chunking strategy defaults to markdown and serializes on submit", async () => {
  const onCreated = renderForm();
  await screen.findByLabelText(/LLM 配置/);
  const select = screen.getByLabelText(/切片方式/) as HTMLSelectElement;
  expect(select.value).toBe("markdown");
  await userEvent.selectOptions(screen.getByLabelText(/LLM 配置/), "1");
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "md-kb");
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));

  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const last = captured[captured.length - 1]?.body as { settings_yaml: string };
  expect(JSON.parse(last.settings_yaml).chunking.strategy).toBe("markdown");
});

test("overlap input is disabled in markdown mode, enabled for tokens", async () => {
  renderForm();
  await screen.findByLabelText(/LLM 配置/);
  const overlap = screen.getByLabelText(/^overlap$/) as HTMLInputElement;
  expect(overlap.disabled).toBe(true); // default markdown -> overlap not used
  await userEvent.selectOptions(screen.getByLabelText(/切片方式/), "tokens");
  expect(overlap.disabled).toBe(false); // tokens -> overlap relevant again
  await userEvent.selectOptions(screen.getByLabelText(/切片方式/), "markdown");
  expect(overlap.disabled).toBe(true);
});
```

Note: the matcher is anchored (`/^overlap$/`) so it matches only the `overlap` field's label, never the `切片方式` selector — whose accessible name includes the hint text and would otherwise also match a bare `/overlap/` query.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npm test -- src/components/KbForm.test.tsx`
Expected: the 2 new tests FAIL (`切片方式` label not found).

- [ ] **Step 3: Add the strategy selector and disable overlap when not token-mode**

In `web/src/components/KbForm.tsx`, inside the `分块 Chunking` `<details>` panel. Find:

```tsx
        <div className="mt-3 grid grid-cols-3 gap-3">
          <Field label="size">
```

Insert a strategy row immediately before that grid `<div>`:

```tsx
        <div className="mt-3">
          <Field
            label="切片方式"
            hint={
              s.chunking.strategy !== "tokens" ? "结构切分不使用此项" : undefined
            }
          >
            <select
              className="select"
              value={s.chunking.strategy}
              onChange={(e) =>
                set("chunking", { ...s.chunking, strategy: e.target.value })
              }
            >
              <option value="markdown">按结构（段落 / 表格不断开）</option>
              <option value="tokens">按 token 数</option>
            </select>
          </Field>
        </div>
```

Then make the `overlap` field reflect markdown mode. Find the overlap `<Field>`:

```tsx
          <Field label="overlap">
            <input
              className="input"
              type="number"
              value={s.chunking.overlap}
              onChange={(e) =>
                set("chunking", { ...s.chunking, overlap: Number(e.target.value) })
              }
            />
          </Field>
```

Replace it with (adds `disabled`):

```tsx
          <Field label="overlap">
            <input
              className="input"
              type="number"
              value={s.chunking.overlap}
              disabled={s.chunking.strategy !== "tokens"}
              onChange={(e) =>
                set("chunking", { ...s.chunking, overlap: Number(e.target.value) })
              }
            />
          </Field>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npm test -- src/components/KbForm.test.tsx`
Expected: PASS (all KbForm tests, including the 2 new ones).

- [ ] **Step 5: Type-check + full frontend suite**

Run: `cd web && npm run build && npm test`
Expected: `tsc -b && vite build` succeeds; all vitest tests pass.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/KbForm.tsx web/src/components/KbForm.test.tsx
git commit -m "feat(web): chunking-strategy selector on KB form

Adds a 切片方式 select (按结构 / 按 token 数); overlap is disabled in markdown
mode with a hint that structure chunking ignores overlap.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Definition of Done

- `uv run pytest -q` and `uv run ruff check .` are green.
- `cd web && npm run build && npm test` are green.
- A new KB created via the dashboard defaults to `chunking.strategy = "markdown"`; existing KBs (no `strategy` key) and API-created KBs still chunk with `tokens`.
- The chunker never bisects a sentence or a table row; oversized tables repeat their header on every chunk.
