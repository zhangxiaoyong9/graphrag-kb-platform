from kb_platform.llm.events import (
    Done, Error, StreamEvent, TextDelta, ToolCallDelta, Usage,
)


def test_event_fields():
    assert TextDelta(text="hi").text == "hi"
    tc = ToolCallDelta(index=0, id="c1", name="search", args_chunk='{"q":')
    assert tc.index == 0 and tc.args_chunk == '{"q":'
    u = Usage(prompt_tokens=3, completion_tokens=5)
    assert u.prompt_tokens == 3 and u.completion_tokens == 5
    assert Done() == Done()
    e = Error(message="boom", retriable=True)
    assert e.retriable is True


def test_stream_event_union_membership():
    for ev in (TextDelta("x"), ToolCallDelta(1), Usage(), Done(), Error("e", False)):
        assert isinstance(ev, StreamEvent.__args__)  # union membership sanity
