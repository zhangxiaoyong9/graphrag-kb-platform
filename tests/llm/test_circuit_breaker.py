from kb_platform.llm.circuit_breaker import CircuitBreaker


def test_closed_allows_and_success_resets():
    cb = CircuitBreaker(failure_threshold=3, open_seconds=30)
    assert cb.state == "closed" and cb.allow() is True
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # under threshold
    cb.record_success()
    assert cb.state == "closed"


def test_opens_after_threshold_then_half_open_after_ttl():
    cb = CircuitBreaker(failure_threshold=2, open_seconds=30)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False  # still open
    # simulate TTL elapse
    cb._opened_at -= 31
    assert cb.allow() is True  # half-open admits one
    assert cb.state == "half_open"
    cb.record_failure()
    assert cb.state == "open"  # back to open


def test_half_open_success_closes(monkeypatch):
    cb = CircuitBreaker(failure_threshold=1, open_seconds=30)
    cb.record_failure()
    assert cb.state == "open"
    cb._opened_at -= 31
    assert cb.allow() is True
    cb.record_success()
    assert cb.state == "closed"
