def test_recorder_accumulates_and_serializes():
    from kb_platform.graph.cost_capture import CostRecorder

    r = CostRecorder()
    r.add(model="deepseek-chat", prompt_tokens=100, completion_tokens=50, cost=0.001)
    r.add(model="deepseek-chat", prompt_tokens=200, completion_tokens=10, cost=0.002)
    js = r.to_json()
    import json

    d = json.loads(js)
    # Same-model adds aggregate into one item; tokens sum; cost sums:
    assert len(d["items"]) == 1
    item = d["items"][0]
    assert item["model"] == "deepseek-chat"
    assert item["prompt_tokens"] == 300
    assert item["completion_tokens"] == 60
    assert abs(item["estimated_cost_usd"] - 0.003) < 1e-9
    assert abs(d["total_usd"] - 0.003) < 1e-9


def test_recorder_unknown_cost_makes_total_none():
    """A model with ANY unknown-cost call -> that model's cost (and the total) is None."""
    from kb_platform.graph.cost_capture import CostRecorder
    import json

    r = CostRecorder()
    r.add(model="mystery-model", prompt_tokens=10, completion_tokens=5, cost=None)
    d = json.loads(r.to_json())
    assert d["total_usd"] is None
    assert d["items"][0]["estimated_cost_usd"] is None
    assert d["items"][0]["prompt_tokens"] == 10  # tokens still recorded


def test_completion_wrapper_captures_usage(monkeypatch):
    """A wrapped completion's completion_async records response.usage into the current recorder."""
    import asyncio
    import json

    from kb_platform.graph.cost_capture import CostCapturingCompletion, use_recorder

    # Stub the registry lookup so cost is deterministic regardless of live data.
    monkeypatch.setattr(
        "kb_platform.graph.cost_capture._compute_cost",
        lambda model_id, pt, ct: 0.0,
    )

    class FakeUsage:
        prompt_tokens = 120
        completion_tokens = 30

    class FakeResp:
        usage = FakeUsage()
        output = "ok"

    class FakeInner:
        async def completion_async(self, **kw):
            return FakeResp()

    async def main():
        with use_recorder() as rec:
            wrapper = CostCapturingCompletion(FakeInner(), model_id="deepseek-chat")
            resp = await wrapper.completion_async(messages="hi", response_format=None)
            assert resp.output == "ok"  # passthrough unchanged
        return rec

    rec = asyncio.run(main())
    d = json.loads(rec.to_json())
    assert d["items"][0]["prompt_tokens"] == 120
    assert d["items"][0]["completion_tokens"] == 30


def test_completion_wrapper_no_recorder_no_error():
    """Without an active recorder, the wrapper must not capture or raise."""
    import asyncio

    from kb_platform.graph.cost_capture import CostCapturingCompletion, current_recorder

    class FakeUsage:
        prompt_tokens = 5
        completion_tokens = 5

    class FakeResp:
        usage = FakeUsage()

    class FakeInner:
        async def completion_async(self, **kw):
            return FakeResp()

    async def main():
        assert current_recorder() is None
        wrapper = CostCapturingCompletion(FakeInner(), model_id="deepseek-chat")
        resp = await wrapper.completion_async(messages="hi")
        assert resp.usage.prompt_tokens == 5

    asyncio.run(main())


def test_completion_wrapper_unknown_model_never_raises(monkeypatch):
    """Missing usage / unknown model must never raise inside the wrapper."""
    import asyncio

    from kb_platform.graph.cost_capture import CostCapturingCompletion, use_recorder

    # Force _compute_cost to return None (unknown model).
    monkeypatch.setattr(
        "kb_platform.graph.cost_capture._compute_cost",
        lambda model_id, pt, ct: None,
    )

    class FakeResp:
        usage = None  # provider omitted usage

    class FakeInner:
        async def completion_async(self, **kw):
            return FakeResp()

    async def main():
        with use_recorder() as rec:
            wrapper = CostCapturingCompletion(FakeInner(), model_id="mystery")
            resp = await wrapper.completion_async(messages="hi")
            assert isinstance(resp, FakeResp)  # passthrough, usage=None recorded nothing
        return rec

    rec = asyncio.run(main())
    assert not rec  # nothing recorded because usage was None
