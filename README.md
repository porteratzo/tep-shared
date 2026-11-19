# tep-shared

Small, table-free Python utilities: hierarchical configuration loading, logging with request correlation,
an API response envelope, HTTP and wire helpers, and a few reconnect/auth building blocks. Distribution name
`tep-shared`, import name `shared`. Python 3.12+, MIT licensed.

It is a plain library, not a service: no entry point, no HTTP API, no tables, and it imports no application code.

## Install

Pin a release tag (the package is not published on PyPI):

```bash
pip install "tep-shared @ git+https://github.com/porteratzo/tep-shared.git@v0.1.0"
```

The core needs only `omegaconf` and `python-dotenv`. Anything heavier is an extra; install the ones for the
modules you import:

| Extra | Brings | Needed by |
|---|---|---|
| `tep-shared[flask]` | `flask` | `shared.core.api_response`, `shared.core.auth` |
| `tep-shared[db]` | `sqlalchemy` | `shared.core.db` (`make_engine`, `session_scope`) |
| `tep-shared[redis]` | `redis` | `shared.camera_service` (`CameraServiceClient`) |
| `tep-shared[dev]` | all three plus lint and test tools | development |

`tests/test_declared_extras.py` pins this split against the code.

## What is in it

- **Configuration** ([`core/config_loader.py`](src/shared/core/config_loader.py)) — `ConfigLoader` merges
  `database`, `redis`, `services`, `plc`, `storage` and `pipeline` `.yaml` files (whichever exist) under
  `<project root>/config/` with environment variables and a `.env` file.
- **Project root** ([`core/paths.py`](src/shared/core/paths.py)) — `resolve_project_root()`: the `root=` argument, then
  `TEP_PROJECT_ROOT`, then a development fallback for an editable install from a checkout with a top-level
  `config/`, else `ProjectRootError`. It never uses the current directory. `ConfigLoader` and `log_setup` anchor
  `config/`, relative sqlite and storage paths, and logs to it.
- **Logging** ([`core/log_setup.py`](src/shared/core/log_setup.py)) — `setup_logging(service_name)` and
  `add_correlation_id_middleware(app)` for `X-Request-ID` correlation across services.
- **API envelope** ([`core/api_response.py`](src/shared/core/api_response.py)) — `success_response` / `error_response`,
  the `{"success": bool, "data": ..., "error": ...}` shape.
- **Engine and session helpers** ([`core/db.py`](src/shared/core/db.py)) — `make_engine` (SQLite WAL, busy timeout,
  foreign keys) and `session_scope` (open, roll back on error, always close). No tables.
- **Auth** ([`core/auth.py`](src/shared/core/auth.py)) — config-password and signed-session-cookie role checks for Flask apps.
- **Other helpers** — `reconnect.py` (`ReconnectBackoff`), `http_utils.py`, `image_paths.py`, `camera_stream.py`,
  and `camera_service.py`.

For a guided tour of the design see [`LEARNING.md`](LEARNING.md).

## Configuration

Priority, highest first: environment variables → `.env` file → YAML files in `config/` → in-code defaults.
YAML values may use `${VAR:default}` placeholders.

```python
from shared.core.config_loader import ConfigLoader

cfg = ConfigLoader()          # root from TEP_PROJECT_ROOT; or ConfigLoader(root="/srv/app")
cfg.load()                    # one instance per process, never per request

redis = cfg.get_redis_config()
db_url = cfg.get_database_url()               # relative sqlite:/// paths are anchored at the project root
frames_dir = cfg.get_storage_path("images.camera_frames")   # absolute Path, directory created if missing
port = int(cfg.get("services.my_service.port", 5010))
```

| Method | Returns |
|---|---|
| `get(key, default=None)` | Dot-separated lookup, e.g. `get("redis.streams.camera_frames.block_ms", 1000)` |
| `get_database_url()` | SQLAlchemy URL; relative `sqlite:///` paths are made absolute |
| `get_redis_config()` | `{host, port, db, password, max_connections, socket_timeout, socket_connect_timeout}` |
| `get_redis_stream_config(name)` | `{name, block_ms, consumer_group, maxlen, ...}` for one stream |
| `get_service_url(name)` | `services.<name>.url`, else `http://<host>:<port>` (defaults `localhost` and `8080`) |
| `get_storage_path(key)` | absolute `Path` for a `storage.*` key |
| `get_plc_config()` | the fields under `plc.<type>` plus `type` |
| `get_auth_config()` | `{maintenance_password, admin_password, secret_key}` |
| `warn_insecure_defaults()` | logs a warning for each auth credential still at a well-known placeholder |
| `reload()` | re-reads all files |

The library ships **no credentials**. `get_auth_config()` reads `ADMIN_PASSWORD`, `MAINTENANCE_PASSWORD` and
`SECRET_KEY` (environment first, then the `auth` block of your YAML) and raises `ConfigError` if one is unset.
A blank password never authenticates.

Call `setup_logging` **before** importing Flask: Flask registers its own handlers at import time, and configuring
logging afterwards silently misconfigures them.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-lint.txt
pip install -e ".[dev]"

pytest                       # fails under 80% coverage
black --check . && isort --check-only . && flake8 . && mypy src/shared
```

The tests are hermetic: `tests/conftest.py` no-ops `load_dotenv`, restores `os.environ` after each test and
points `TEP_PROJECT_ROOT` at a per-session temp directory. `tests/fixtures/config/` holds small fixture YAML;
its placeholder passwords exist only to exercise `warn_insecure_defaults()`.

## Releasing

Releases are git tags (`v0.1.0`, ...). Bump `version` in `pyproject.toml`, tag the commit, and consumers pin the tag.
CI runs lint, mypy and the tests on every push and pull request.

## License

MIT, see [`LICENSE`](LICENSE).
