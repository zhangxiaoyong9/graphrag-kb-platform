"""Tests for kb_platform.input.doc_reader (markitdown-based extraction)."""

from kb_platform.input.doc_reader import read_document


def test_read_document_txt():
    assert "hello" in read_document(b"hello world", "note.txt")


def test_read_document_markitdown_fallback_on_decode():
    """Binary-ish input still yields text (markitdown or decode fallback), never raises."""
    out = read_document(b"\xff\xfe\x00x", "weird.bin")
    assert isinstance(out, str)


def test_read_document_never_raises_on_garbage():
    out = read_document(b"\x00\x01\x02garbage\xff\xfe", "x.dat")
    assert isinstance(out, str)
