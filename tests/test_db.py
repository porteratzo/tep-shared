"""``make_engine`` and ``session_scope`` (the table-free engine/session helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from shared.core.db import make_engine, session_scope

pytestmark = pytest.mark.unit


def _pragma(engine, name: str):  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return conn.execute(text(f"PRAGMA {name}")).scalar()


def test_sqlite_engine_enables_wal_busy_timeout_and_foreign_keys(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}")
    try:
        assert _pragma(engine, "journal_mode") == "wal"
        assert _pragma(engine, "busy_timeout") == 30_000
        assert _pragma(engine, "foreign_keys") == 1
    finally:
        engine.dispose()


def test_engine_is_usable_from_another_thread(tmp_path: Path) -> None:
    import threading

    engine = make_engine(f"sqlite:///{tmp_path / 'b.db'}")
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    engine.dispose()
    assert not errors


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


def test_session_scope_closes_without_rollback_on_success() -> None:
    session = _FakeSession()
    with session_scope(lambda: session) as got:
        assert got is session
    assert session.calls == ["close"]


def test_session_scope_rolls_back_then_closes_and_propagates() -> None:
    session = _FakeSession()
    with pytest.raises(ValueError, match="boom"):
        with session_scope(lambda: session):
            raise ValueError("boom")
    assert session.calls == ["rollback", "close"]
