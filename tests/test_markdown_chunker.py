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
