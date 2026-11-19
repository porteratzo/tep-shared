"""Tests for shared.core.log_setup — handler level split, rotation, and metrics channel."""

import json
import logging
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

import pytest

from shared.core import log_setup

pytestmark = pytest.mark.unit


@pytest.fixture()
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point LOG_DIR at a temp dir and restore the root/metrics loggers afterwards."""
    monkeypatch.setattr(log_setup, "LOG_DIR", tmp_path)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    yield tmp_path

    for handler in root.handlers:
        handler.close()
    root.handlers = saved_handlers
    root.setLevel(saved_level)

    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("metrics."):
            logger = logging.getLogger(name)
            for handler in logger.handlers:
                handler.close()
            logger.handlers = []


def _console_handler(root: logging.Logger) -> logging.StreamHandler:
    return next(
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    )


def _file_handler(root: logging.Logger) -> RotatingFileHandler:
    return next(h for h in root.handlers if isinstance(h, RotatingFileHandler))


@pytest.fixture()
def metrics_service_name() -> str:
    """A unique service name per test.

    get_metrics_logger() caches its logger by name (`if metrics_logger.handlers:
    return metrics_logger`), so reusing a literal name like "test_svc" across
    tests relies entirely on the isolated_log_dir teardown sweep to reset that
    cache — fragile under a full combined test-suite run where other global
    logging state can interact with it. A fresh name per test sidesteps the
    shared cache entirely: get_metrics_logger always takes the full setup path.
    """
    return f"test_svc_{uuid.uuid4().hex[:8]}"


class TestSetupLogging:
    def test_file_and_console_levels_are_independent(self, isolated_log_dir: Path) -> None:
        """DEBUG-level file logging must not raise the console handler's level too."""
        log_setup.setup_logging("test_svc", level=logging.DEBUG)

        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert _console_handler(root).level == logging.INFO

    def test_console_level_override(self, isolated_log_dir: Path) -> None:
        log_setup.setup_logging("test_svc", level=logging.DEBUG, console_level=logging.ERROR)

        assert _console_handler(logging.getLogger()).level == logging.ERROR

    def test_env_vars_take_precedence_over_arguments(
        self, isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        monkeypatch.setenv("LOG_LEVEL_CONSOLE", "ERROR")

        log_setup.setup_logging("test_svc", level=logging.DEBUG, console_level=logging.INFO)

        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert _console_handler(root).level == logging.ERROR

    def test_file_handler_rotates(self, isolated_log_dir: Path) -> None:
        log_setup.setup_logging("test_svc", level=logging.DEBUG)

        file_handler = _file_handler(logging.getLogger())
        assert file_handler.maxBytes == log_setup._LOG_MAX_BYTES
        assert file_handler.backupCount == log_setup._LOG_BACKUP_COUNT

    def test_debug_record_reaches_file_not_console(
        self, isolated_log_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        log_setup.setup_logging("test_svc", level=logging.DEBUG)
        logging.getLogger("test_svc.mod").debug("only in file")

        captured = capsys.readouterr()
        assert "only in file" not in captured.out

        log_contents = (isolated_log_dir / "test_svc.log").read_text()
        assert "only in file" in log_contents


class TestMetricsLogger:
    def test_metrics_logger_does_not_propagate(
        self, isolated_log_dir: Path, metrics_service_name: str
    ) -> None:
        metrics = log_setup.get_metrics_logger(metrics_service_name)
        assert metrics.propagate is False

    def test_metrics_logger_writes_json_lines(
        self, isolated_log_dir: Path, metrics_service_name: str
    ) -> None:
        metrics = log_setup.get_metrics_logger(metrics_service_name)
        log_setup.log_metric(metrics, "frame_processed", frame_id=42, latency_ms=12.3)

        line = (isolated_log_dir / f"{metrics_service_name}.metrics.log").read_text().strip()
        record = json.loads(line)
        assert record["event"] == "frame_processed"
        assert record["frame_id"] == 42
        assert record["latency_ms"] == 12.3
        assert "ts" in record

    def test_metrics_never_reach_console(
        self, isolated_log_dir: Path, metrics_service_name: str, capsys: pytest.CaptureFixture
    ) -> None:
        log_setup.setup_logging(metrics_service_name, level=logging.DEBUG)
        metrics = log_setup.get_metrics_logger(metrics_service_name)
        log_setup.log_metric(metrics, "frame_processed", frame_id=1)

        captured = capsys.readouterr()
        assert "frame_processed" not in captured.out

    def test_repeated_calls_reuse_handlers(self, isolated_log_dir: Path, metrics_service_name: str) -> None:
        first = log_setup.get_metrics_logger(metrics_service_name)
        second = log_setup.get_metrics_logger(metrics_service_name)

        assert first is second
        assert len(first.handlers) == 1


class TestLogDirResolvesAbsolute:
    """Regression test for a bug where pipeline_maker's log file was written to
    <service dir>/data/logs/ instead of <project root>/data/logs/, making it invisible
    to the dashboard's Logs page (which reads LOG_DIR from this same module).

    A service's ``api_server.py`` used to do
    ``sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))`` before
    importing ``shared``. When launched as ``python api_server.py`` from within the
    service directory, __file__ is the relative string
    "api_server.py", so that computed to the relative path "..", inserted ahead of
    the properly editable-installed absolute-path ``shared`` package. Python then
    resolved ``shared`` via that relative entry, giving log_setup.py a relative
    __file__ whose .parent chain resolved against the current working directory
    instead of this file's real location.

    This reproduces that scenario directly against shared/log_setup.py: import it
    via a relative sys.path entry from a different CWD, and confirm LOG_DIR still
    lands on the real project-root data/logs directory instead of a CWD-relative one.
    """

    def test_log_dir_absolute_even_via_relative_sys_path_entry(self) -> None:
        import os
        import subprocess
        import sys

        repo_root = Path(os.environ["TEP_PROJECT_ROOT"]).resolve()
        cwd = repo_root / "svc"
        cwd.mkdir(exist_ok=True)

        # Isolate from any LOGS_DIR already set in this environment/.env file,
        # so the assertion below pins the *default* resolution, not whatever
        # override happens to be active.
        env = {k: v for k, v in os.environ.items() if k != "LOGS_DIR"}

        script = (
            "import sys; sys.path.insert(0, '..'); "
            "from shared.core.log_setup import LOG_DIR; "
            "print(LOG_DIR)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        log_dir = Path(result.stdout.strip())

        assert log_dir.is_absolute()
        assert log_dir == repo_root / "data" / "logs"


class TestLogDirHonorsEnvOverride:
    """SH-B6: LOG_DIR must resolve under the repo root by default (previously
    resolved one level above it, disagreeing with every script/systemd
    unit/doc that assumes <repo>/data/logs), and must honor a real LOGS_DIR
    env var override — computed once at import time, so this spawns a fresh
    interpreter rather than monkeypatching os.environ post-import."""

    def _log_dir_in_subprocess(self, env_overrides: dict) -> Path:
        import os
        import subprocess
        import sys

        repo_root = Path(os.environ["TEP_PROJECT_ROOT"]).resolve()
        env = {k: v for k, v in os.environ.items() if k != "LOGS_DIR"}
        env.update(env_overrides)
        result = subprocess.run(
            [sys.executable, "-c", "from shared.core.log_setup import LOG_DIR; print(LOG_DIR)"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        return Path(result.stdout.strip())

    def test_default_resolves_under_repo_root(self) -> None:
        import os

        repo_root = Path(os.environ["TEP_PROJECT_ROOT"]).resolve()
        assert self._log_dir_in_subprocess({}) == repo_root / "data" / "logs"

    def test_logs_dir_env_var_overrides_default(self, tmp_path: Path) -> None:
        assert self._log_dir_in_subprocess({"LOGS_DIR": str(tmp_path)}) == tmp_path
