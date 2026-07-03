"""Driver pool: reuse-by-identity + close_all. The real neo4j driver is mocked
so this runs without the [neo4j] extra installed."""

from unittest.mock import AsyncMock, MagicMock


def test_get_driver_reuses_by_identity(monkeypatch):
    from kb_platform.neo4j import driver_pool

    driver_pool._reset_for_test()
    created = MagicMock()
    fake_driver = MagicMock(name="asyncdriver")
    created.return_value = fake_driver
    monkeypatch.setattr(driver_pool, "_build_driver", created)

    a = driver_pool.get_driver("bolt://x", "u", "p")
    b = driver_pool.get_driver("bolt://x", "u", "p")
    assert a is b
    assert created.call_count == 1


def test_get_driver_distinct_identity_creates_new(monkeypatch):
    from kb_platform.neo4j import driver_pool

    driver_pool._reset_for_test()
    factory = MagicMock(side_effect=lambda *a, **kw: MagicMock(name="d"))
    monkeypatch.setattr(driver_pool, "_build_driver", factory)

    driver_pool.get_driver("bolt://x", "u", "p")
    driver_pool.get_driver("bolt://x", "u", "rotated")  # password changed
    driver_pool.get_driver("bolt://y", "u", "p")        # uri changed
    assert factory.call_count == 3


async def test_close_all_closes_every_driver(monkeypatch):
    from kb_platform.neo4j import driver_pool

    driver_pool._reset_for_test()
    d1, d2 = AsyncMock(), AsyncMock()
    monkeypatch.setattr(driver_pool, "_build_driver", lambda *a, **kw: d1 if len(driver_pool._DRIVERS) == 0 else d2)
    driver_pool.get_driver("bolt://x", "u", "p")
    driver_pool.get_driver("bolt://y", "u", "p")
    await driver_pool.close_all()
    d1.close.assert_awaited_once()
    d2.close.assert_awaited_once()
    assert driver_pool._DRIVERS == {}
