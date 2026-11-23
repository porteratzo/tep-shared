"""
Shared configuration loader using OmegaConf.

Provides hierarchical configuration merging from YAML files and environment variables.
"""

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict, cast

from dotenv import load_dotenv
from omegaconf import DictConfig, OmegaConf

from shared.core.paths import resolve_project_root

logger = logging.getLogger(__name__)

_ENV_INTERP_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):([^}]*)\}")

# Values that YAML 1.1 (what OmegaConf's loader implements) already types as
# something other than a plain string. An env-provided value matching one of
# these, case-insensitively, is spliced in unquoted so it keeps the type it
# has always had. Anything else is quoted (see _quote_env_value) so it can't
# be reinterpreted as YAML syntax.
_CANONICAL_NUMBER_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_CANONICAL_BOOL_RE = re.compile(
    r"^(true|false|yes|no|on|off|y|n)$",
    re.IGNORECASE,
)

# Well-known weak credential values. There is deliberately NO fallback to these in this library:
# a project supplies its own credentials via env/.env/YAML, and ``warn_insecure_defaults`` only flags a
# configured value that is still one of these.
_INSECURE_DEFAULTS = {
    "auth.maintenance_password": "maintenance",
    "auth.admin_password": "admin",
    "auth.secret_key": "change-this-key-in-production",
}


# COMPAT(D-E4-5): renamed service sections, new name -> old name (see ConfigLoader.service_key).
_LEGACY_SERVICE_KEYS = {"pipeline_editor": "pipeline_maker"}


class ConfigError(RuntimeError):
    """A required configuration value is missing."""


# ---------------------------------------------------------------------------
# TypedDicts for structured config return values
# ---------------------------------------------------------------------------


class RedisConfig(TypedDict):
    host: str
    port: int
    db: int
    password: Optional[str]
    max_connections: int
    socket_timeout: int
    socket_connect_timeout: int


class RedisStreamConfig(TypedDict):
    name: str
    block_ms: int
    consumer_group: str
    maxlen: int


class AuthConfig(TypedDict):
    maintenance_password: str
    admin_password: str
    secret_key: str


class _PLCConfigRequired(TypedDict):
    type: str  # "allen_bradley" | "modbus" | "siemens_s7" | "mock"


class PLCConfig(_PLCConfigRequired, total=False):
    # all types — `tag` is in the protocol's native address format (see config/plc.yaml)
    tag: str
    timeout: int
    reconnect_interval: float
    reconnect_max_interval: float
    # raw tag value (case-insensitive) -> "normal" | "anomaly"; unmapped values
    # raise no alarm (see config/plc.yaml, AD-B1)
    flag_map: Dict[str, str]
    # allen_bradley / modbus / siemens_s7
    ip: str
    # allen_bradley / siemens_s7
    slot: int
    # modbus / siemens_s7
    port: int
    # modbus
    unit_id: int
    # siemens_s7
    rack: int
    # mock
    default_value: str
    random_failures: bool
    failure_rate: float


def _quote_env_value(value: str) -> str:
    """Render an env var's raw text as safe YAML source for splicing into config.

    A canonical number or boolean spelling is inserted unquoted, so it keeps
    parsing as the same type it always has. Everything else is inserted as a
    JSON-quoted string — valid YAML flow-scalar syntax — so characters that
    are meaningful to the YAML parser (``:``, a trailing ``:``, a leading
    ``'``/``"``/``[``/``{``, a `` #`` comment) can't break parsing or get
    silently truncated, and a value that happens to look like a
    number/date/null doesn't get silently retyped. ``${`` is escaped
    to OmegaConf's literal-brace escape so a value containing it isn't
    misread as an interpolation reference after YAML parsing.
    """
    escaped = value.replace("${", "\\${")
    if _CANONICAL_NUMBER_RE.match(value) or _CANONICAL_BOOL_RE.match(value):
        return escaped
    return json.dumps(escaped)


# COMPAT(D-E4-5): renamed env vars, new name -> old name; the old one is honoured with a warning.
_LEGACY_ENV_VARS = {f"PIPELINE_EDITOR_{k}": f"PIPELINE_MAKER_{k}" for k in ("HOST", "PORT", "URL", "DEBUG")}


def _substitute_env_vars(content: str) -> str:
    """Replace ``${VAR_NAME:default}`` env-var interpolations with resolved values.

     OmegaConf uses the ``${resolver:args}`` syntax and treats the part before
     the first colon as a *resolver name*.  Plain environment variable names
     (e.g. ``DB_URL``) are not registered resolvers, so OmegaConf raises
     ``UnsupportedInterpolationType``.  We therefore expand these references
     ourselves before handing the YAML content to OmegaConf.

     Only an env-provided value is quoted/escaped via ``_quote_env_value``
    ; the ``:default`` text is spliced in exactly as before,
     since it's authored by us in the YAML files, not by an operator's
     environment.
    """

    def _replace(m: re.Match) -> str:
        env_value = os.getenv(m.group(1))
        if env_value is None and m.group(1) in _LEGACY_ENV_VARS:
            legacy = _LEGACY_ENV_VARS[m.group(1)]
            env_value = os.getenv(legacy)
            if env_value is not None:
                logger.warning("Environment variable %s is deprecated; use %s", legacy, m.group(1))
        if env_value is None:
            return m.group(2)
        return _quote_env_value(env_value)

    return _ENV_INTERP_RE.sub(_replace, content)


class ConfigLoader:
    """
    Load and merge configuration from multiple sources.

    Priority (highest to lowest):
    1. Environment variables
    2. .env file
    3. YAML config files
    4. Default values in YAML
    """

    def __init__(self, config_dir: Optional[str] = None, root: Optional[str] = None):
        """
        Initialize configuration loader.

        Args:
            config_dir: Path to configuration directory. Defaults to ``<project root>/config``.
            root: Project root that relative sqlite/storage paths are anchored to. Defaults to
                ``$TEP_PROJECT_ROOT``, then a development checkout; raises ``ProjectRootError`` rather
                than guessing (see ``shared.core.paths``).
        """
        self._root_arg = root
        # Load .env file first
        load_dotenv()

        # Determine config directory
        if config_dir is None:
            self.config_dir: Path = self.root / "config"
        else:
            self.config_dir = Path(config_dir)
        self._config: Optional[DictConfig] = None

    @property
    def root(self) -> Path:
        """The project root relative paths are anchored to (resolved on first use)."""
        return resolve_project_root(self._root_arg)

    def load(self) -> DictConfig:
        """
        Load all configuration files and merge them.

        Returns:
            Merged configuration as OmegaConf DictConfig
        """
        if self._config is not None:
            return self._config

        # Load all YAML files in config directory
        config_files = [
            "database.yaml",
            "redis.yaml",
            "services.yaml",
            "plc.yaml",
            "storage.yaml",
            "pipeline.yaml",
        ]

        configs = []
        for filename in config_files:
            filepath = self.config_dir / filename
            if filepath.exists():
                raw = filepath.read_text(encoding="utf-8")
                preprocessed = _substitute_env_vars(raw)
                cfg = OmegaConf.load(io.StringIO(preprocessed))
                configs.append(cfg)

        # Merge all configs
        if configs:
            self._config = cast(DictConfig, OmegaConf.merge(*configs))
        else:
            self._config = cast(DictConfig, OmegaConf.create({}))

        return self._config

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-separated key.

        Args:
            key: Dot-separated key (e.g., 'database.url')
            default: Default value if key not found

        Returns:
            Configuration value, or ``default`` when the key is absent.

        Raises:
            ConfigError: the key exists but resolving it fails (e.g. a broken
                ``${...}`` interpolation). Returning ``default`` there would silently
                replace a configured value with the caller's fallback (D-SH2).
        """
        if self._config is None:
            self.load()

        assert self._config is not None
        try:
            return OmegaConf.select(self._config, key, default=default)
        except Exception as e:
            raise ConfigError(f"Config lookup failed for key {key!r}: {e}") from e

    def get_database_url(self) -> str:
        """Get database URL.

        For relative SQLite URLs (``sqlite:///relative/path``), the path is
        resolved to an absolute path anchored at the repo root so the same
        database file is used regardless of the working directory.  The parent
        directory is created automatically if it does not yet exist.
        """
        url: str = str(self.get("database.url", "sqlite:///data/manufacturing_inspection.db"))

        # Only transform relative sqlite paths (three slashes = relative).
        # Absolute paths use four slashes: sqlite:////abs/path
        # ``sqlite:///:memory:`` is SQLite's in-memory DB, not a relative file path.
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
            rel_path = url[len("sqlite:///") :]
            abs_path = (self.root / rel_path).resolve()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{abs_path}"

        return url

    def get_redis_config(self) -> RedisConfig:
        """Get Redis connection configuration."""
        return {
            "host": self.get("redis.host", "localhost"),
            "port": int(self.get("redis.port", 6379)),
            "db": self.get("redis.db", 0),
            "password": self.get("redis.password", None) or None,
            "max_connections": self.get("redis.max_connections", 50),
            "socket_timeout": self.get("redis.socket_timeout", 5),
            "socket_connect_timeout": self.get("redis.socket_connect_timeout", 5),
        }

    def get_redis_stream_config(self, stream_name: str = "camera_frames") -> RedisStreamConfig:
        """Get Redis stream configuration."""
        base_path = f"redis.streams.{stream_name}"
        return {
            "name": self.get(f"{base_path}.name", stream_name),
            "block_ms": self.get(f"{base_path}.block_ms", 1000),
            "consumer_group": self.get(f"{base_path}.consumer_group", f"{stream_name}_group"),
            "maxlen": int(self.get(f"{base_path}.maxlen", 100)),
        }

    def get_service_url(self, service_name: str) -> str:
        """
        Get service URL.

        Args:
            service_name: Service name (e.g., 'ai_service')

        Returns:
            Service URL
        """
        service_name = self.service_key(service_name)
        url: Optional[str] = self.get(f"services.{service_name}.url")
        if url:
            return url

        # Build URL from host and port
        host: str = str(self.get(f"services.{service_name}.host", "localhost"))
        port: int = int(self.get(f"services.{service_name}.port", 8080))
        return f"http://{host}:{port}"

    def service_key(self, service_name: str) -> str:
        """Return the ``services.<key>`` section name to read for ``service_name``.

        COMPAT(D-E4-5): the pipeline editor's section was renamed ``pipeline_maker`` ->
        ``pipeline_editor``. A config that still has only the old section keeps working, with a
        deprecation warning; remove the alias once no deployed ``services.yaml`` uses it.
        """
        legacy = _LEGACY_SERVICE_KEYS.get(service_name)
        if legacy and self.get(f"services.{service_name}") is None and self.get(f"services.{legacy}") is not None:
            logger.warning(
                "Config section services.%s is deprecated; rename it to services.%s", legacy, service_name
            )
            return legacy
        return service_name

    def get_storage_path(self, path_type: str) -> Path:
        """
        Get storage path.

        Relative paths are resolved to an absolute path anchored at the repo
        root (same behaviour as ``get_database_url``) so that every service
        reads/writes to the same location regardless of its working directory.

        Args:
            path_type: Type of storage (e.g., 'images.camera_frames', 'models.path')

        Returns:
            Absolute Path object
        """
        path_str = self.get(f"storage.{path_type}", f"data/{path_type.replace('.', '/')}")
        path = Path(path_str)

        # Anchor relative paths to repo root so all services share the same
        # directories regardless of their working directory.
        if not path.is_absolute():
            path = (self.root / path).resolve()

        # Create directory if it doesn't exist
        path.mkdir(parents=True, exist_ok=True)

        return path

    def get_auth_config(self) -> AuthConfig:
        """Get role-based authentication configuration.

        Reads straight from the environment variable when one is set,
        bypassing the YAML/OmegaConf round-trip entirely for these three
        security-sensitive values — a password or secret must reach auth
        code exactly as the operator typed it, whether it happens to look
        like a number, a boolean, ``null``, or contains YAML/OmegaConf
        syntax such as ``${``. Falls back to the YAML-loaded
        value (still ``str()``-coerced for the same reason) when no env var
        overrides it, and raises ``ConfigError`` when neither is set.
        """
        return {
            "maintenance_password": self._credential("maintenance_password", "MAINTENANCE_PASSWORD"),
            "admin_password": self._credential("admin_password", "ADMIN_PASSWORD"),
            "secret_key": self._credential("secret_key", "SECRET_KEY"),
        }

    def _credential(self, key: str, env_var: str) -> str:
        """Env var, else the YAML ``auth.<key>`` value; raise ``ConfigError`` if neither is set."""
        from_env = os.getenv(env_var)
        if from_env is not None:
            return from_env
        configured = self.get(f"auth.{key}")
        if configured is None:
            raise ConfigError(
                f"Missing credential 'auth.{key}': set {env_var} or add it to the auth block of "
                "config/services.yaml. This library has no built-in default."
            )
        return str(configured)

    def get_plc_config(self) -> PLCConfig:
        """Get PLC configuration."""
        plc_type: str = str(self.get("plc.type", "allen_bradley"))
        raw = self.get(f"plc.{plc_type}", {})
        if isinstance(raw, DictConfig):
            raw = OmegaConf.to_container(raw, resolve=True)  # a plain-dict default `{}` is not a config node
        config: PLCConfig = cast(PLCConfig, {"type": plc_type, **(raw if isinstance(raw, dict) else {})})
        return config

    def warn_insecure_defaults(self) -> None:
        """Log a WARNING for each auth credential still at its insecure default, and an ERROR per blank role.

        A role whose configured password is blank/whitespace can never log in (TS-1: fail closed,
        see ``shared.core.auth.passwords_match``); the ERROR names the role so the operator knows why.
        """
        if self._config is None:
            self.load()
        auth = self.get_auth_config()
        for role, key in (("maintenance", "maintenance_password"), ("admin", "admin_password")):
            if not auth[key].strip():  # type: ignore[literal-required]
                logger.error(
                    "SECURITY: the %s role's password is blank, so nobody can log in as %s. "
                    "Set %s_PASSWORD (or auth.%s in config/services.yaml) to a non-empty value.",
                    role,
                    role,
                    role.upper(),
                    key,
                )
        for key, default_val in _INSECURE_DEFAULTS.items():
            if self.get(key) == default_val:
                logger.warning(
                    "SECURITY: config key '%s' is using its insecure default value. "
                    "Set it via config/services.yaml's auth block or the corresponding "
                    "environment variable.",
                    key,
                )

    def reload(self) -> None:
        """Reload configuration from files."""
        self._config = None
        self.load()
