"""Markdown structure-aware chunker.

Never cuts inside a minimal unit (sentence / table row): packs whole markdown
blocks (headings, paragraphs, tables, lists, code fences) up to ``size`` tokens;
a block that alone exceeds ``size`` is decomposed (paragraph -> sentences, table
-> rows with the header repeated, list -> items), and a single sentence still
over budget falls back to a token split. Code blocks are never split (fence
integrity).

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
