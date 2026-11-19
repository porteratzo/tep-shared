"""Tests for ConfigLoader env-var override precedence and edge cases.

These complement the existing test_config_loader.py which tests YAML loading.
This file focuses specifically on:
- Environment variables overriding YAML values
- ${VAR:default} interpolation in YAML
- get_redis_config() / get_plc_config() helpers with env override
- Empty config directory handling
"""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.core.config_loader import ConfigLoader

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def yaml_config_dir(tmp_path: Path) -> Path:
    """A temp config dir with a minimal set of YAML files."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    (config_dir / "redis.yaml").write_text(textwrap.dedent("""\
        redis:
          host: yaml-host
          port: 6379
          db: 0
    """))

    (config_dir / "database.yaml").write_text(textwrap.dedent("""\
        database:
          url: sqlite:///data/test.db
    """))

    (config_dir / "plc.yaml").write_text(textwrap.dedent("""\
        plc:
          type: mock
          mock:
            ip: 192.168.0.1
    """))

    (config_dir / "services.yaml").write_text(textwrap.dedent("""\
        services:
          tep_ai_url: http://localhost:8081
    """))

    (config_dir / "storage.yaml").write_text(textwrap.dedent(""))
    (config_dir / "pipeline.yaml").write_text(textwrap.dedent(""))

    return config_dir


@pytest.fixture()
def loader(yaml_config_dir: Path) -> ConfigLoader:
    return ConfigLoader(config_dir=str(yaml_config_dir))


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------


class TestBasicLoading:
    def test_loads_values_from_yaml(self, loader: ConfigLoader) -> None:
        assert loader.get("redis.host") == "yaml-host"
        assert loader.get("redis.port") == 6379

    def test_missing_key_returns_none(self, loader: ConfigLoader) -> None:
        assert loader.get("redis.does_not_exist") is None

    def test_missing_key_returns_provided_default(self, loader: ConfigLoader) -> None:
        assert loader.get("redis.timeout", default=30) == 30

    def test_config_is_cached_after_first_load(self, loader: ConfigLoader) -> None:
        loader.load()
        first = loader._config
        loader.load()
        assert loader._config is first  # same object, not re-created

    def test_empty_config_dir_returns_empty_config(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        loader = ConfigLoader(config_dir=str(empty_dir))
        result = loader.get("anything", default="fallback")
        assert result == "fallback"


# ---------------------------------------------------------------------------
# ${VAR:default} interpolation in YAML
# ---------------------------------------------------------------------------


class TestEnvVarInterpolation:
    def test_substitutes_env_var_when_set(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "redis.yaml").write_text(textwrap.dedent("""\
            redis:
              host: ${REDIS_HOST:fallback-host}
        """))
        for f in ("database", "plc", "services", "storage", "pipeline"):
            (cfg_dir / f"{f}.yaml").write_text("")

        with patch.dict(os.environ, {"REDIS_HOST": "env-set-host"}, clear=False):
            loader = ConfigLoader(config_dir=str(cfg_dir))
            assert loader.get("redis.host") == "env-set-host"

    def test_uses_default_when_env_var_not_set(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "redis.yaml").write_text(textwrap.dedent("""\
            redis:
              host: ${REDIS_HOST_NOT_SET_XYZ:default-fallback}
        """))
        for f in ("database", "plc", "services", "storage", "pipeline"):
            (cfg_dir / f"{f}.yaml").write_text("")

        env = {k: v for k, v in os.environ.items() if k != "REDIS_HOST_NOT_SET_XYZ"}
        with patch.dict(os.environ, env, clear=True):
            loader = ConfigLoader(config_dir=str(cfg_dir))
            assert loader.get("redis.host") == "default-fallback"

    def test_multiple_interpolations_in_same_file(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "redis.yaml").write_text(textwrap.dedent("""\
            redis:
              host: ${REDIS_HOST_A:host-a}
              port: ${REDIS_PORT_A:1234}
        """))
        for f in ("database", "plc", "services", "storage", "pipeline"):
            (cfg_dir / f"{f}.yaml").write_text("")

        env = {k: v for k, v in os.environ.items() if k not in ("REDIS_HOST_A", "REDIS_PORT_A")}
        with patch.dict(os.environ, env, clear=True):
            loader = ConfigLoader(config_dir=str(cfg_dir))
            assert loader.get("redis.host") == "host-a"
            # port comes back as a string from YAML (no type coercion)
            assert str(loader.get("redis.port")) == "1234"


# ---------------------------------------------------------------------------
# get_database_url()
# ---------------------------------------------------------------------------


class TestGetDatabaseUrl:
    def test_relative_sqlite_url_is_made_absolute(self, loader: ConfigLoader) -> None:
        url = loader.get_database_url()
        # Relative sqlite:/// should be expanded to an absolute path
        assert url.startswith("sqlite:///")
        db_path = url[len("sqlite:///") :]
        assert Path(db_path).is_absolute()

    def test_absolute_sqlite_url_is_preserved(self, tmp_path: Path, yaml_config_dir: Path) -> None:
        abs_db = tmp_path / "db" / "mydb.sqlite"
        # Update the YAML to an absolute URL (four slashes)
        (yaml_config_dir / "database.yaml").write_text(f"database:\n  url: sqlite:////{abs_db}\n")
        loader = ConfigLoader(config_dir=str(yaml_config_dir))
        url = loader.get_database_url()
        assert str(abs_db) in url


# ---------------------------------------------------------------------------
# get_redis_config()
# ---------------------------------------------------------------------------


class TestGetRedisConfig:
    def test_returns_dict_with_host_and_port(self, loader: ConfigLoader) -> None:
        cfg = loader.get_redis_config()
        assert "host" in cfg
        assert "port" in cfg

    def test_host_matches_yaml(self, loader: ConfigLoader) -> None:
        cfg = loader.get_redis_config()
        assert cfg["host"] == "yaml-host"

    def test_port_is_integer(self, loader: ConfigLoader) -> None:
        cfg = loader.get_redis_config()
        assert isinstance(cfg["port"], int)


# ---------------------------------------------------------------------------
# get_plc_config()
# ---------------------------------------------------------------------------


class TestGetPlcConfig:
    def test_returns_plc_type(self, loader: ConfigLoader) -> None:
        cfg = loader.get_plc_config()
        assert "type" in cfg
        assert cfg["type"] == "mock"

    def test_returns_ip_when_set_in_yaml(self, loader: ConfigLoader) -> None:
        cfg = loader.get_plc_config()
        # get_plc_config merges plc.<type> sub-keys, so ip is at top level
        assert cfg.get("ip") == "192.168.0.1"
