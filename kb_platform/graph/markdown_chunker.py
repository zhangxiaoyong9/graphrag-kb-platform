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
