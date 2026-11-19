"""
shared/core/log_setup.py — Central logging configuration for all tep services.

Single source of truth for:
  - Where log files are written  (…/data/logs/)
  - Log format (including optional request_id for correlation)
  - How to configure the root logger for a given service
  - Flask middleware for injecting X-Request-ID correlation headers
  - A separate metrics channel for structured, high-volume data

Usage in each service entry-point (before any third-party imports):

    from shared.core.log_setup import setup_logging
    setup_logging("tep_ai")          # writes tep_ai.log
    setup_logging("camera_broker")   # writes camera_broker.log

To add correlation-ID middleware to a Flask app:

    from shared.core.log_setup import add_correlation_id_middleware
    add_correlation_id_middleware(app)

The **file** handler and the **console** handler are controlled independently, so
turning on rich, DEBUG-level detail for troubleshooting never floods the terminal:

    setup_logging("tep_ai", level=logging.DEBUG)   # file: DEBUG, console: still INFO

Both are also tunable per-deployment without a code change via env vars, which take
precedence over the function arguments:

    LOG_LEVEL=DEBUG           # file/root level
    LOG_LEVEL_CONSOLE=WARNING # console level (defaults to INFO regardless of LOG_LEVEL)

The log file rotates at 10MB (5 backups kept) so DEBUG-level detail doesn't grow
unbounded — see `config/storage.yaml`'s `storage.logs.max_bytes`/`backup_count`,
which these defaults mirror.

For metrics (counters, timings, per-frame stats) that should be rich and structured
but must never compete with debug logs on the terminal, use a metrics logger instead.
It writes JSON-lines to its own rotating file and never propagates to the console:

    from shared.core.log_setup import get_metrics_logger, log_metric

    metrics = get_metrics_logger("tep_ai")
    log_metric(metrics, "inference", frame_id=frame_id, latency_ms=42.1, score=0.87)
"""

import contextvars
import json
import logging
import os
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from shared.core.paths import ProjectRootError, resolve_project_root

if TYPE_CHECKING:
    from flask import Flask
    from flask.wrappers import Response

# ---------------------------------------------------------------------------
# The log directory is resolved relative to this file so it is independent of
# the working directory and the depth of the calling entry-point module.
# ``.resolve()`` is required: if this module is ever reached via a relative
# sys.path entry (e.g. an entry-point script inserting a CWD-relative ".."),
# __file__ is relative too, and the .parent walk below would silently resolve
# against the current working directory instead of this file's real location.
#
#   see shared.core.paths: the project root is explicit, env-provided or a checkout, never the cwd
#
# Honors LOGS_DIR when it's a real, exported env var, matching storage.logs.
# path's ``${LOGS_DIR:data/logs}`` in config/storage.yaml. Deliberately does
# NOT call python-dotenv's load_dotenv() to also pick up an un-exported
# ``.env``-file-only value: this module is imported at collection/startup
# time by nearly everything (including most of the test suite), and
# load_dotenv() mutates process-global os.environ as a side effect — doing
# that here would leak .env's LOG_LEVEL/LOGS_DIR/etc. into every test in the
# same pytest session, most of which assume a pristine environment unless
# they explicitly opt in via monkeypatch. ConfigLoader (built later, by each
# service's own entry point) already calls load_dotenv() for the full
# priority chain; a real deployment exports LOGS_DIR (e.g. via systemd's
# EnvironmentFile=) rather than relying on an unexported .env-only value
# being visible this early. Previously LOG_DIR always resolved one level
# *above* the repo: every script, systemd unit, and doc pointed at
# <repo>/data/logs, so the app's own rotated file silently landed somewhere
# nothing else read from.
# ---------------------------------------------------------------------------
_logs_dir_setting = os.environ.get("LOGS_DIR", "data/logs")
_logs_dir_path = Path(_logs_dir_setting)


def _resolve_log_dir() -> Path:
    if _logs_dir_path.is_absolute():
        return _logs_dir_path
    try:
        return (resolve_project_root() / _logs_dir_path).resolve()
    except ProjectRootError:
        # An installed package with no project root: never write into the cwd; use the user's cache dir.
        return (Path.home() / ".cache" / "tep" / _logs_dir_path.name).resolve()


LOG_DIR: Path = _resolve_log_dir()

_DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s"

# Mirrors config/storage.yaml storage.logs.max_bytes / backup_count.
_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
_LOG_BACKUP_COUNT = 5

_NOISY_LOGGERS = [
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "PIL",
    "PIL.PngImagePlugin",
]

# ---------------------------------------------------------------------------
# Correlation ID context variable — set per-request by the Flask middleware.
# Defaults to "-" when no request is active (background threads, startup, etc.)
# ---------------------------------------------------------------------------
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class CorrelationIdFilter(logging.Filter):
    """Inject the current request_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.request_id = request_id_var.get("-")  # type: ignore[attr-defined]
        return True


def _level_from_env(env_var: str, default: int) -> int:
    """Resolve a log level from an env var name (e.g. "DEBUG"), falling back to *default*."""
    name = os.environ.get(env_var)
    if not name:
        return default
    resolved = logging.getLevelName(name.upper())
    return resolved if isinstance(resolved, int) else default


def setup_logging(
    service_name: str,
    level: int = logging.INFO,
    console_level: Optional[int] = None,
    fmt: str = _DEFAULT_FORMAT,
) -> None:
    """Configure the root logger for *service_name*.

    Creates a rotating ``LOG_DIR/<service_name>.log`` (10MB x 5 backups) and a stdout
    stream handler. The two are leveled independently: *level* (or the ``LOG_LEVEL`` env
    var, which takes precedence) controls the file/root level and can safely be set to
    DEBUG for rich troubleshooting detail; *console_level* (or ``LOG_LEVEL_CONSOLE``)
    controls the terminal and defaults to INFO regardless of the file level, so raising
    file verbosity never bloats the CLI.

    Safe to call multiple times — ``basicConfig`` with ``force=True`` re-applies
    handlers; the correlation-ID filter is idempotent because we check before adding.
    """
    level = _level_from_env("LOG_LEVEL", level)
    console_level = _level_from_env(
        "LOG_LEVEL_CONSOLE", console_level if console_level is not None else logging.INFO
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_file = LOG_DIR / f"{service_name}.log"

    corr_filter = CorrelationIdFilter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.addFilter(corr_filter)
    stream_handler.setLevel(console_level)

    file_handler = RotatingFileHandler(str(log_file), maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT)
    file_handler.addFilter(corr_filter)

    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[stream_handler, file_handler],
        force=True,
    )

    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_metrics_logger(service_name: str) -> logging.Logger:
    """Return a metrics logger for *service_name* that never touches the console.

    Writes JSON-lines to a rotating ``LOG_DIR/<service_name>.metrics.log``. The logger
    has ``propagate=False`` so records never reach the root logger's console handler,
    regardless of the console level configured by ``setup_logging`` — metrics stay rich
    and structured on disk without adding CLI noise.

    Safe to call multiple times: handlers are only attached on first call per
    *service_name*, so repeated calls (e.g. from multiple modules) return the same
    configured logger instead of duplicating handlers.
    """
    metrics_logger = logging.getLogger(f"metrics.{service_name}")
    if metrics_logger.handlers:
        return metrics_logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        str(LOG_DIR / f"{service_name}.metrics.log"),
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    metrics_logger.addHandler(handler)
    metrics_logger.setLevel(logging.DEBUG)
    metrics_logger.propagate = False
    return metrics_logger


def log_metric(metrics_logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one JSON-line metric record: ``{"event": ..., "ts": ..., **fields}``.

    Use lightweight JSON-serializable values in *fields* (numbers, strings, bools);
    anything else is coerced with ``str()`` via the ``default`` fallback.
    """
    record = {"event": event, "ts": time.time(), **fields}
    metrics_logger.info(json.dumps(record, default=str))


def add_correlation_id_middleware(app: "Flask") -> None:
    """Register before/after_request hooks on *app* to propagate X-Request-ID.

    - before_request: reads X-Request-ID from incoming headers (or generates a UUID)
      and stores it in both ``flask.g`` and the ``request_id_var`` context variable.
    - after_request: echoes the ID back as a response header so callers can correlate.

    Outgoing HTTP calls made with ``requests`` should forward the ID manually:

        from shared.core.log_setup import request_id_var
        headers = {"X-Request-ID": request_id_var.get("-")}
        requests.post(url, headers=headers, ...)
    """
    from flask import g
    from flask import request as _req

    @app.before_request
    def _set_request_id() -> None:
        rid = _req.headers.get("X-Request-ID") or str(uuid.uuid4())
        g.request_id = rid
        request_id_var.set(rid)

    @app.after_request
    def _echo_request_id(response: "Response") -> "Response":
        rid = getattr(g, "request_id", "-")
        response.headers["X-Request-ID"] = rid
        return response
