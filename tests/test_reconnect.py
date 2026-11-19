"""Unit tests for shared.core.reconnect.ReconnectBackoff (promoted from plc_integration)."""

import pytest

from shared.core.reconnect import ReconnectBackoff

pytestmark = pytest.mark.unit


def _backoff(**kw: float) -> tuple:
    now = [100.0]
    return ReconnectBackoff(clock=lambda: now[0], **kw), now


def test_ready_before_any_failure() -> None:
    b, _ = _backoff(initial=2.0, maximum=10.0)
    assert b.ready()


def test_failure_closes_window_then_reopens() -> None:
    b, now = _backoff(initial=2.0, maximum=10.0)
    b.record_failure()
    assert not b.ready()
    now[0] += 2.0
    assert b.ready()


def test_delay_doubles_and_is_capped() -> None:
    b, now = _backoff(initial=2.0, maximum=5.0)
    waits = []
    for _ in range(4):
        b.record_failure()
        t0 = now[0]
        while not b.ready():
            now[0] += 0.5
        waits.append(now[0] - t0)
    assert waits == [2.0, 4.0, 5.0, 5.0]


def test_reset_restores_initial_delay() -> None:
    b, now = _backoff(initial=2.0, maximum=60.0)
    b.record_failure()
    b.record_failure()
    b.reset()
    assert b.ready()
    b.record_failure()
    now[0] += 2.0
    assert b.ready()


def test_non_positive_initial_disables_reconnect() -> None:
    b, _ = _backoff(initial=0.0)
    assert not b.enabled and not b.ready()
