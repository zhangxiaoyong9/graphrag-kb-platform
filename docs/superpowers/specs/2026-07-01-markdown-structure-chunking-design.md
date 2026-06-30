# Markdown structure-aware chunking

**Date:** 2026-07-01
**Status:** Approved (design)

## Problem

Chunking today uses graphrag's `TokenChunker` (`build_default_adapter` →
`ChunkerType.Tokens`, `size=1200 / overlap=100`): it slices strictly by token
count and **is blind to document structure**. A single cut can land in the
middle of a sentence, split a paragraph across two chunks, or sever a markdown
table between two rows. That fragments the context fed to entity extraction and
destroys table semantics.

The user's desired principle: **never cut inside a minimal unit.** Paragraphs
and tables should stay whole; when a unit is too large to fit a chunk, splitting
down to the sentence (for prose) or row (for tables) level is acceptable, as
long as no sentence or row is itself bisected. (`"句子也行,保证完整就行"` —
sentences are an acceptable boundary; integrity is the invariant.)

`graphrag_chunking` ships only two chunkers and neither satisfies this:

- `TokenChunker` — cuts anywhere by token count.
- `SentenceChunker` — `nltk.sent_tokenize` then **one chunk per sentence**
  (`create_chunk_results` makes a chunk per sentence; `size`/`overlap` are
  ignored entirely, `__init__` doesn't even accept them). It fragments prose
  into thousands of tiny chunks, mangles markdown tables (tokenizes cell text
  as sentences), and uses nltk's English punkt — poor for the Chinese docs this
  dashboard serves.

## Scope

A new opt-in **structure-aware chunker** that respects markdown structure
(headings, paragraphs, tables, lists, code blocks), plus a per-KB
`chunking.strategy` selector to choose between it and the existing token chunker.

**In scope:**

- New module `kb_platform/graph/markdown_chunker.py` — pure-Python, **zero
  graphrag imports**; tokenizer `encode`/`decode` injected (same seam as
  `TokenChunker`).
- `build_default_adapter` / `build_adapter_from_settings` select the chunker by
  `chunking.strategy`.
- KB settings gain `chunking.strategy` (`"tokens"` | `"markdown"`); KB config
  form exposes the choice.

**Out of scope:**

- Replacing the token chunker or changing the default (see Decision D2).
- Non-markdown structure (HTML/ XML parsing). `markitdown` already emits
  markdown; plain-text uploads degrade gracefully to sentence packing.
- Changing `chunk_id = sha512(text)` or the `ChunkText` shape — the new chunker
  returns objects with `.text` and the adapter's existing `_hash(tc.text)` path
  is unchanged, so delta detection and idempotency are untouched.

## Design

### The invariant

Every emitted chunk is built by **packing whole structural units**; a cut only
ever happens at a unit boundary. The unit hierarchy, smallest-first:

1. **sentence** (prose) — a single sentence is never bisected.
2. **table row** — a single row is never bisected.
3. **paragraph / list-item / code-line** — built from the above.
4. **structural block** (heading, paragraph, table, list, code fence).

When a block fits, it stays whole. When it does not, it is decomposed by the
next-smaller unit and those units are packed. Only when a *single sentence* (or
single row, single line) still exceeds `chunk_size` do we fall back to a
token-level split of that one unit.

### Block segmentation (`markdown_chunker.py`)

Split the input into an ordered list of blocks by scanning lines:

- **Blank line** ends the current block.
- A block is classified by its content:
  - `code` — fenced (```` ``` ````…```` ``` ````); kept whole (the fence makes
    it self-delimiting; blank lines inside are absorbed).
  - `table` — every line matches `^\s*\|.*\|\s*$` (markdown pipe table).
  - `heading` — a line matching `^#{1,6}\s` (ATX heading).
  - `list` — every line matches `^\s*([-*+]|\d+\.)\s` (bullet/ordered list).
  - `paragraph` — anything else (a run of contiguous non-blank, non-special
    lines).

(Implementation note: classify a raw blank-separated block by majority/first
line so a multi-line paragraph containing an incidental `|` — e.g. `"a | b"`
which does not start with `|` — stays a paragraph. Only runs of true pipe lines
become tables.)

### Greedy packing

```
for each block:
    if block is a heading:
        hold it in pending_heading (do not commit yet)        # see "Heading attachment"
        continue
    text = (pending_heading + "\n\n" + block.text) if pending_heading else block.text
    if token_count(block.text) > chunk_size:
        flush current chunk
        emit split_oversized(block, prefix=pending_heading)   # heading prepends to first sub-piece
        pending_heading = None
        continue
    pending_heading = None
    if current and token_count(current + "\n\n" + text) > chunk_size:
        flush current; start new current = text
    else:
        current = current + "\n\n" + text if current else text
flush current
# trailing pending_heading (doc ends with a heading) → emit as its own chunk
```

`token_count` uses the injected `encode`: `len(encode(s))`. Re-encoding the
*prospective* current chunk at each decision point is O(chunk_size) per block
(current is bounded to one chunk), not O(doc) — fine for large inputs.

### Heading attachment

A heading never ends a chunk on its own (a chunk starting with body text under
no heading robs extraction of section context). A heading is buffered and
prepended to the next non-heading block, so it travels with the content it
introduces. Multiple consecutive headings (`# H1` then `## H2`) all prepend to
the next block. A heading at end-of-document with no following content is
emitted as its own chunk.

### Oversized-block decomposition (`split_oversized`)

`split_oversized(block, *, prefix="")` emits pieces ≤ `chunk_size`; `prefix`
(a buffered heading) is prepended to the **first** emitted piece only, so a
heading survives even when the block it introduces must be split.

- **paragraph** → sentence-split, then greedily pack sentences into ≤
  `chunk_size` chunks. A single sentence still > `chunk_size` → token-split
  that sentence (`encode` → slice → `decode`, mirroring `TokenChunker`).
- **table** → split into header (`lines[0]`, plus `lines[1]` when it is a
  separator row matching `^\s*\|[\s:|-]+\|\s*$`) and body rows. Each emitted
  piece = **header + a group of body rows**, so every table chunk is itself a
  valid, self-describing markdown table. Rows are packed until the next row
  would exceed `chunk_size`; a single row > `chunk_size` is emitted alone (a
  row is never bisected).
- **list** → split by item (line); pack items; a single item > `chunk_size`
  token-splits.
- **code** → split by line; pack lines; a single line > `chunk_size` emitted
  alone (don't bisect a code line).

### Sentence splitting (Chinese + Western, no nltk)

A self-contained regex splitter — **no nltk/punkt dependency** (nltk's default
punkt is English; this dashboard serves Chinese docs). Split on terminators
`。！？；.!?;` (keeping the terminator with the sentence) and on hard newlines,
then drop empties. This handles Chinese (`。！？`) and Western (`.!?`) prose in
one pass without a model download or a per-language config.

### Chunker shape

```python
@dataclass
class MarkdownChunk:
    text: str
    index: int = 0

class MarkdownChunker:
    def __init__(self, *, size: int, encode, decode=None) -> None: ...
    def chunk(self, text: str) -> list[MarkdownChunk]: ...
```

`GraphRagAdapter.chunk_document` already does
`ChunkText(chunk_id=_hash(tc.text), text=tc.text) for tc in self._chunker.chunk(text)`
— it reads only `.text`, so `MarkdownChunk` slots in with no adapter change.

### Wiring

`build_default_adapter` (`kb_platform/graph/graphrag_adapter.py`): new kwarg
`chunk_strategy: str = "tokens"`. After building the tokenizer:

```python
if chunk_strategy == "markdown":
    from kb_platform.graph.markdown_chunker import MarkdownChunker
    chunker = MarkdownChunker(size=chunk_size, encode=tokenizer.encode,
                              decode=tokenizer.decode)
else:
    chunker = create_chunker(ChunkingConfig(type=ChunkerType.Tokens, ...), ...)
```

`build_adapter_from_settings`: pass
`chunk_strategy=chunking.get("strategy", "tokens")` through. `overlap` is
ignored in markdown mode (boundaries are already semantic; a hard overlap would
duplicate half-sentences into the next chunk and violate the integrity
invariant) — `size` is reused as the token budget.

`markdown_chunker.py` imports **only stdlib** (`re`, `dataclasses`); the
tokenizer is injected, so the "`graphrag_adapter.py` is the only graphrag
coupling point" rule is preserved.

### Settings / API

`chunking.strategy` is a plain key in the opaque `settings_json` blob — **no DB
migration** (settings_json is unstructured text; `api/models.py` and
`db/models.py` don't validate its keys). Resolution:

- `assemble_kb_settings` already passes `content.get("chunking", {})` through
  unchanged → `strategy` rides along automatically.
- `build_adapter_from_settings` reads `chunking.get("strategy", "tokens")`.

### Frontend

`web/src/lib/kb-settings.ts`:

- `KbFormState.chunking` gains `strategy: string`.
- `DEFAULTS.chunking.strategy = "markdown"` — a blank form (new KB) defaults to
  structure-aware chunking.
- `buildSettings`: **force-write** `strategy` (always emit `chunking.strategy`,
  NOT via `addIf`) so the chosen value persists even when it equals the form
  default. This guarantees a KB created via the form stores its strategy
  explicitly (a new KB → `strategy: "markdown"`).
- `parseSettings`: `strategy: f(ch, "strategy", "tokens")` — the **read-back
  default is `"tokens"`** (NOT `DEFAULTS.chunking.strategy`), matching the
  backend default so a KB whose stored settings predate this key (no
  `strategy`) accurately reflects what the backend will do.

`web/src/components/KbForm.tsx` (the `分块 Chunking` panel, ~line 226): add a
segmented control / select labelled **`切片方式`** with options
`按 token 数(默认)` → `"tokens"` and `按结构(段落/表格不断开)` → `"markdown"`.
When `markdown` is selected, the `overlap` field is visually disabled with a
hint `结构切分不使用 overlap` (still serialized if previously set, but ignored
server-side). Match surrounding Chinese copy.

`web/src/components/KbForm.test.tsx`: extend the existing chunking assertion to
cover `strategy` round-trip through `buildSettings`/`parseSettings`.

### Tests

Backend (`pytest`), new file `tests/test_markdown_chunker.py` — pure unit tests
against `MarkdownChunker` with a fake `encode` (e.g. `lambda s: s.split()` for
whitespace-"token" counting) so behavior is deterministic without a tokenizer:

- A short paragraph + table + paragraph packs into chunks where **no paragraph
  or table is split** (boundaries land only between blocks).
- A table larger than `chunk_size` is split **by row**, and **every chunk
  repeats the header + separator**; no row is bisected.
- A paragraph larger than `chunk_size` is split **at sentence boundaries**
  (verify with Chinese `。` and Western `.`); no sentence is bisected.
- A single sentence larger than `chunk_size` is token-split (graceful fallback).
- A heading is attached to the following block; no chunk ends with a bare
  heading (except a trailing heading at EOF).
- Plain-text input (no markdown structure) degrades to sentence packing.
- `chunk_strategy="markdown"` vs `"tokens"`: `build_adapter_from_settings`
  wires `MarkdownChunker` vs the graphrag `TokenChunker` (assert the adapter's
  injected chunker type).

Frontend (`vitest`): `KbForm` selecting `markdown` serializes
`settings.chunking.strategy === "markdown"`; `parseSettings` round-trips it.

## Decisions

- **D1 — per-KB strategy, not global.** A `chunking.strategy` selector keeps
  the token chunker as-is for existing setups and lets each KB opt in. Backward
  compatible; no migration.
- **D2 — new KBs default to `"markdown"`; existing / API-created KBs stay
  `"tokens"`.** The frontend form defaults a blank form to `"markdown"` and
  force-writes the key, so KBs created via the dashboard get structure-aware
  chunking. The **backend** default (`build_adapter_from_settings` /
  `build_default_adapter`, both `chunking.get("strategy", "tokens")`) stays
  `"tokens"`, so existing KBs (no `strategy` key in their settings) and KBs
  created via the API without the key are unchanged — non-breaking. The two
  defaults are intentionally split: form-default = markdown (going-forward
  quality), backend/read-back default = tokens (do-not-surprise-existing-KBs).
- **D3 — no nltk.** Self-contained Chinese+Western regex sentence splitter,
  avoiding a punkt model dependency and English-only segmentation.
- **D4 — tables repeat their header on every chunk** so each piece is a valid
  standalone markdown table.

## Risks / notes

- Token counts are approximate at chunk boundaries only insofar as `len(encode(joined))`
  is exact — we re-encode the prospective chunk, so the budget check is exact;
  cost is bounded by `chunk_size` per block.
- `overlap` is silently ignored in markdown mode (documented in the UI hint).
  If a future caller depends on overlap, that's token-mode only.
- MarkItDown occasionally emits tables with leading/trailing whitespace or
  colspan quirks; the pipe-line regex is permissive (`^\s*\|.*\|\s*$`) and the
  header-repeat logic keys off line position + separator pattern, not column
  count, so minor formatting variance is tolerated.
- Because `chunk_id = sha512(text)` is unchanged, switching an **existing** KB
  from `tokens` to `markdown` changes every chunk's text (hence id) → a full
  re-index, not an incremental no-op. That's expected (the chunking changed)
  and is the user's explicit choice; the incremental path's delta detection
  still works correctly because it hashes the new chunk text.
