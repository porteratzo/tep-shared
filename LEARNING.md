# Learning Guide — `tep-shared`

`tep-shared` is a library, not a service: no entry point, no HTTP API. The file worth understanding in
depth (covered below) is **[`config_loader.py`](src/shared/core/config_loader.py)**, a loader that merges YAML files,
a `.env` file and environment variables. A few smaller modules round out the package and are used without
much ceremony:

- **[`log_setup.py`](src/shared/core/log_setup.py)** — `setup_logging(service_name)` (call it before importing Flask, which
  registers its own handlers at import time) and `add_correlation_id_middleware(app)`, which stamps every
  request with an `X-Request-ID` (held in `request_id_var`, a `contextvars.ContextVar`) so one inbound request
  can be traced across service boundaries in the logs.
- **[`api_response.py`](src/shared/core/api_response.py)** — `success_response(data)` / `error_response(msg, code)`, the
  `{"success": bool, "data": ..., "error": ...}` envelope.
- **[`http_utils.py`](src/shared/core/http_utils.py)** — `correlation_headers()`, which reads the current request's
  correlation ID back out of `request_id_var` so an outbound HTTP call can propagate it.
- **[`db.py`](src/shared/core/db.py)** — `make_engine` (SQLite WAL mode, busy timeout, foreign keys) and `session_scope`
  (open / rollback-on-error / close). No tables: the package never defines a schema.
- **[`paths.py`](src/shared/core/paths.py)** — `resolve_project_root()`: the `root=` argument, then `TEP_PROJECT_ROOT`, then an
  editable-checkout fallback, else `ProjectRootError`; never the current directory.
- **[`reconnect.py`](src/shared/core/reconnect.py)**, **[`camera_stream.py`](src/shared/core/camera_stream.py)**,
  **[`image_paths.py`](src/shared/core/image_paths.py)**, **[`auth.py`](src/shared/core/auth.py)** — small helpers; read the module docstrings.

---

## Tech stack at a glance

| Library | Role |
|---------|------|
| **OmegaConf** | YAML config loading with dot-notation access and env-var interpolation |
| **python-dotenv** | Loads a `.env` file into `os.environ` before OmegaConf reads it |

Flask, SQLAlchemy and Redis are optional extras (`tep-shared[flask]`, `[db]`, `[redis]`).

---

## [`config_loader.py`](src/shared/core/config_loader.py) — `ConfigLoader`

### Why a shared config loader?

Each service needs the same connection strings (Redis host, DB URL, service ports). Without a shared loader, you'd either hard-code these in each service or duplicate environment-variable reading logic. `ConfigLoader` reads all YAML files once and exposes them through a consistent API.

### How it works

```python
loader = ConfigLoader()   # finds config/ under the project root (root=, or TEP_PROJECT_ROOT)
loader.load()             # reads whichever of database/redis/services/plc/storage/pipeline .yaml exist

db_url  = loader.get_database_url()          # "sqlite:////abs/project/data/app.db"
redis   = loader.get_redis_config()          # {"host": "localhost", "port": 6379, ...}
ai_url  = loader.get_service_url("ai_service")  # "http://localhost:8081"
```

Internally it uses **OmegaConf** to merge the YAML files:

```python
configs = [OmegaConf.load(f) for f in yaml_files]
self._config = OmegaConf.merge(*configs)
```

`OmegaConf.merge` combines multiple DictConfig objects — later files can override keys from earlier files (though in practice the YAML files don't overlap).

After merging, `OmegaConf.resolve(self._config)` substitutes any `${oc.env:MY_VAR}` interpolations with actual environment variable values. This is how YAML files can reference env vars without any extra code.

### Dot-notation access

```python
loader.get("services.my_service.port", default=8081)
# equivalent to: config["services"]["my_service"]["port"] with a fallback
```

[`ConfigLoader.get()`](src/shared/core/config_loader.py) wraps `OmegaConf.select(cfg, "services.my_service.port", default=8081)`, which does the dot-splitting and nested lookup.

### [`get_storage_path`](src/shared/core/config_loader.py)

```python
path = loader.get_storage_path("images.camera_frames")
# reads storage.images.camera_frames from YAML
# creates the directory if it doesn't exist
# returns a Path object
```

The directory creation side effect is convenient: every service can call this at startup without worrying whether the storage directories exist yet.

### One instance per process — there is no shared `get_config()`

`config_loader.py` exports no module-level singleton. Build and load one `ConfigLoader` per process, at
startup, and reuse it for every route and job; it reads YAML from disk, so constructing one per request is
wasteful:

```python
_cfg = ConfigLoader()
_cfg.load()
PORT = int(_cfg.get("services.my_service.port", 5010))
```

Each process re-reads the files independently, which is fine: they are static files, not a source of contention.

---

## Testing patterns in use

- **The `.env` tier needs the real dotenv.** `tests/conftest.py` replaces `dotenv.load_dotenv` with a no-op for the
  whole session so a developer's `.env` can't leak in. `tests/test_config_precedence.py` routes
  `shared.core.config_loader.load_dotenv` to `dotenv.main.load_dotenv(<tmp .env>)` so env > `.env` > YAML default can
  be tested with conflicting values.
- **A blank configured password never logs in.** `passwords_match` returns False when the expected (or supplied)
  password is blank/whitespace, so `ADMIN_PASSWORD=` in a `.env` locks that role out instead of opening it.
- **Write truth tables out in the test.** `tests/test_auth.py` spells the role ranks itself; reading them from
  `ROLE_LEVELS` would make a wrong table pass.
