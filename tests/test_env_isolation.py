"""Regression test for the root conftest.py os.environ leak guard.

ConfigLoader.load() calls python-dotenv's load_dotenv(), which mutates the
real process os.environ with no teardown. Any test that builds a real
ConfigLoader (directly or via app/service code) was leaking .env's values
(e.g. LOG_LEVEL=INFO) into every later test in the same pytest process —
see shared/tests/test_log_setup.py, which failed only when run after such a
test, never in isolation. The root conftest.py's autouse `_restore_os_environ`
fixture snapshots/restores os.environ around every test to close that hole,
regardless of how the leak happens.

These two tests must run in file order (test_a before test_b) to prove it;
there is no pytest-randomly plugin in this repo (see pyproject.toml plugins),
so default top-to-bottom collection order is relied on here.
"""

import os

import pytest

pytestmark = pytest.mark.unit

_SENTINEL = "ZZZ_CONFTEST_ENV_LEAK_REGRESSION"


def test_a_direct_os_environ_mutation_without_monkeypatch() -> None:
    """Mutate os.environ the way load_dotenv() does: directly, no monkeypatch."""
    os.environ[_SENTINEL] = "leaked"
    assert os.environ[_SENTINEL] == "leaked"


def test_b_previous_tests_mutation_did_not_leak() -> None:
    """The root conftest fixture must have restored os.environ after test_a."""
    assert _SENTINEL not in os.environ
