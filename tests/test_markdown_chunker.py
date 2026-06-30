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
