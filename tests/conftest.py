"""Test isolation for tep-shared.

* ``python-dotenv``'s ``load_dotenv`` is replaced with a no-op, so a developer's real ``.env`` can never
  leak into a test. Tests that want the ``.env`` tier route ``config_loader.load_dotenv`` to a temp file.
* ``os.environ`` is restored after every test (``ConfigLoader.load()`` mutates the real environment).
* ``TEP_PROJECT_ROOT`` points at a per-session temp directory, so nothing is written next to the checkout
  and nothing depends on the layout of whatever repository the package happens to be installed from.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import dotenv
import pytest

_SESSION_ROOT: Path | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _SESSION_ROOT
    noop = lambda *args, **kwargs: False  # noqa: E731
    dotenv.load_dotenv = noop
    _SESSION_ROOT = Path(tempfile.mkdtemp(prefix="tep_shared_test_root_"))
    os.environ["TEP_PROJECT_ROOT"] = str(_SESSION_ROOT)


def pytest_unconfigure(config: pytest.Config) -> None:
    if _SESSION_ROOT is not None:
        shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _restore_os_environ() -> Generator[None, None, None]:
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)
