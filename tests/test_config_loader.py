"""Tests for shared.core.config_loader.ConfigLoader"""

import os
from pathlib import Path

import pytest

from shared.core.config_loader import ConfigLoader

pytestmark = pytest.mark.unit

# Small fixture config (tests/fixtures/config) mirroring the shapes ConfigLoader reads
CONFIG_DIR = str(Path(__file__).parent / "fixtures" / "config")


def _placeholder_env_vars() -> set:
    """Every ``${VAR:default}`` name used by the fixture config/*.yaml files."""
    import re

    names: set = set()
    for yaml_file in Path(CONFIG_DIR).glob("*.yaml"):
        names.update(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):", yaml_file.read_text(encoding="utf-8")))
    return names


@pytest.fixture(autouse=True)
def _config_env_is_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert the shipped defaults, so a developer's exported ``REDIS_HOST``/``PLC_TYPE``/...
    (or a CI job's) must not change the outcome."""
    for name in _placeholder_env_vars():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def loader() -> ConfigLoader:
    """ConfigLoader pointing at the fixture config/ YAML files."""
    return ConfigLoader(config_dir=CONFIG_DIR)


# ---------------------------------------------------------------------------
# get() — dot-separated key access
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_existing_key(self, loader: ConfigLoader) -> None:
        """Known key from redis.yaml resolves correctly."""
        assert loader.get("redis.host") == "localhost"

    def test_get_missing_key_returns_default(self, loader: ConfigLoader) -> None:
        """Missing key returns the specified default."""
        value = loader.get("nonexistent.key.path", default="fallback")
        assert value == "fallback"

    def test_get_missing_key_default_none(self, loader: ConfigLoader) -> None:
        """Missing key returns None when no default given."""
        assert loader.get("nonexistent.key.path") is None

    def test_get_nested_key(self, loader: ConfigLoader) -> None:
        """Deeply nested key access works."""
        assert loader.get("redis.port") == 6379

    def test_get_caches_config(self, loader: ConfigLoader) -> None:
        """Second call to get() uses cached config (no re-load)."""
        loader.get("redis.host")
        config_before = loader._config
        loader.get("redis.port")
        assert loader._config is config_before


# ---------------------------------------------------------------------------
# get_database_url()
# ---------------------------------------------------------------------------


class TestGetDatabaseUrl:
    def test_default_is_the_project_data_db_as_an_absolute_sqlite_url(self, loader: ConfigLoader) -> None:
        """``sqlite:///data/...`` in database.yaml is anchored at the repo root (not the cwd): the same file
        for every service. Four slashes = absolute path on POSIX."""
        from shared.core.paths import resolve_project_root

        expected = f"sqlite:///{(resolve_project_root() / 'data' / 'app.db').resolve()}"
        assert loader.get_database_url() == expected
        assert expected.startswith("sqlite:////")

    def test_env_override(self, tmp_path: Path) -> None:
        """DB_URL env var overrides yaml value."""
        db_file = tmp_path / "test.db"
        override_url = f"sqlite:///{db_file}"
        old = os.environ.get("DB_URL")
        try:
            os.environ["DB_URL"] = override_url
            cfg = ConfigLoader(config_dir=CONFIG_DIR)
            assert cfg.get_database_url() == override_url
        finally:
            if old is None:
                os.environ.pop("DB_URL", None)
            else:
                os.environ["DB_URL"] = old

    def test_four_slash_absolute_path_not_modified(self, tmp_path: Path) -> None:
        """sqlite:////abs/path (4 slashes) is returned unchanged — not re-resolved."""
        abs_url = f"sqlite:////{tmp_path}/app.db"
        old = os.environ.get("DB_URL")
        try:
            os.environ["DB_URL"] = abs_url
            cfg = ConfigLoader(config_dir=CONFIG_DIR)
            assert cfg.get_database_url() == abs_url
        finally:
            if old is None:
                os.environ.pop("DB_URL", None)
            else:
                os.environ["DB_URL"] = old


# ---------------------------------------------------------------------------
# get_redis_config()
# ---------------------------------------------------------------------------


class TestGetRedisConfig:
    def test_shipped_defaults(self, loader: ConfigLoader) -> None:
        assert loader.get_redis_config() == {
            "host": "localhost",
            "port": 6379,
            "db": 0,
            "password": None,  # `${REDIS_PASSWORD:}` -> empty -> None, never the string ""
            "max_connections": 50,
            "socket_timeout": 5,
            "socket_connect_timeout": 5,
        }

    def test_empty_redis_password_env_var_means_no_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``REDIS_PASSWORD=`` (set but empty, as .env files do) must not send AUTH with an empty string."""
        monkeypatch.setenv("REDIS_PASSWORD", "")
        assert ConfigLoader(config_dir=CONFIG_DIR).get_redis_config()["password"] is None
        monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
        assert ConfigLoader(config_dir=CONFIG_DIR).get_redis_config()["password"] == "s3cret"

    def test_port_is_int(self, loader: ConfigLoader) -> None:
        cfg = loader.get_redis_config()
        assert isinstance(cfg["port"], int)

    def test_host_is_string(self, loader: ConfigLoader) -> None:
        cfg = loader.get_redis_config()
        assert isinstance(cfg["host"], str)

    def test_env_override_host(self) -> None:
        """REDIS_HOST env var changes the returned host."""
        old = os.environ.get("REDIS_HOST")
        try:
            os.environ["REDIS_HOST"] = "redis-test-host"
            cfg = ConfigLoader(config_dir=CONFIG_DIR)
            assert cfg.get_redis_config()["host"] == "redis-test-host"
        finally:
            if old is None:
                os.environ.pop("REDIS_HOST", None)
            else:
                os.environ["REDIS_HOST"] = old

    def test_non_integer_redis_port_raises(self) -> None:
        """A non-integer REDIS_PORT env var raises ValueError at get_redis_config() time."""
        old = os.environ.get("REDIS_PORT")
        try:
            os.environ["REDIS_PORT"] = "not_a_port"
            cfg = ConfigLoader(config_dir=CONFIG_DIR)
            with pytest.raises(ValueError):
                cfg.get_redis_config()
        finally:
            if old is None:
                os.environ.pop("REDIS_PORT", None)
            else:
                os.environ["REDIS_PORT"] = old


# ---------------------------------------------------------------------------
# get_redis_stream_config()
# ---------------------------------------------------------------------------


class TestGetRedisStreamConfig:
    def test_default_stream_has_required_keys(self, loader: ConfigLoader) -> None:
        cfg = loader.get_redis_stream_config("camera_frames")
        assert "name" in cfg
        assert "block_ms" in cfg
        assert "consumer_group" in cfg
        assert "maxlen" in cfg

    def test_camera_frames_maxlen_comes_from_redis_yaml(self, loader: ConfigLoader) -> None:
        """MD-B4: the stream bound is configured (100), not an unbounded-in-practice 10,000."""
        assert loader.get_redis_stream_config("camera_frames")["maxlen"] == 100

    def test_unknown_stream_returns_defaults(self, loader: ConfigLoader) -> None:
        cfg = loader.get_redis_stream_config("nonexistent_stream")
        assert cfg["name"] == "nonexistent_stream"


# ---------------------------------------------------------------------------
# get_service_url()
# ---------------------------------------------------------------------------


class TestGetServiceUrl:
    def test_ai_service_url_comes_from_the_yaml(self, loader: ConfigLoader) -> None:
        assert loader.get_service_url("ai_service") == "http://localhost:8081"

    def test_unknown_service_falls_back_to_localhost_8080(self, loader: ConfigLoader) -> None:
        assert loader.get_service_url("totally_unknown_service") == "http://localhost:8080"

    def test_without_a_url_the_host_and_port_are_combined(self, tmp_path: Path) -> None:
        (tmp_path / "services.yaml").write_text(
            "services:\n  thing:\n    host: example.org\n    port: 9000\n"
        )
        assert ConfigLoader(config_dir=str(tmp_path)).get_service_url("thing") == "http://example.org:9000"

    def test_an_explicit_url_wins_over_host_and_port(self, tmp_path: Path) -> None:
        (tmp_path / "services.yaml").write_text(
            "services:\n  thing:\n    host: example.org\n    port: 9000\n    url: https://svc.internal/base\n"
        )
        assert ConfigLoader(config_dir=str(tmp_path)).get_service_url("thing") == "https://svc.internal/base"

    def test_host_only_service_uses_the_default_port(self, tmp_path: Path) -> None:
        (tmp_path / "services.yaml").write_text("services:\n  thing:\n    host: example.org\n")
        assert ConfigLoader(config_dir=str(tmp_path)).get_service_url("thing") == "http://example.org:8080"

    def test_env_override_url(self) -> None:
        old = os.environ.get("AI_SERVICE_URL")
        try:
            os.environ["AI_SERVICE_URL"] = "http://custom-host:9999"
            cfg = ConfigLoader(config_dir=CONFIG_DIR)
            assert cfg.get_service_url("ai_service") == "http://custom-host:9999"
        finally:
            if old is None:
                os.environ.pop("AI_SERVICE_URL", None)
            else:
                os.environ["AI_SERVICE_URL"] = old


# ---------------------------------------------------------------------------
# get_storage_path()
# ---------------------------------------------------------------------------


class TestGetStoragePath:
    def test_returns_path_object(self, loader: ConfigLoader) -> None:
        p = loader.get_storage_path("images.camera_frames")
        assert isinstance(p, Path)

    def test_path_is_absolute(self, loader: ConfigLoader) -> None:
        p = loader.get_storage_path("images.camera_frames")
        assert p.is_absolute()


# ---------------------------------------------------------------------------
# get_plc_config()
# ---------------------------------------------------------------------------


class TestGetPlcConfig:
    def test_default_is_the_mock_plc_with_its_flag_map(self, loader: ConfigLoader) -> None:
        cfg = loader.get_plc_config()
        assert cfg["type"] == "mock"
        assert cfg["default_value"] == "PASS"
        assert cfg["flag_map"] == {"PASS": "normal", "FAIL": "anomaly"}
        assert type(cfg["flag_map"]) is dict  # a plain dict, not an OmegaConf node

    def test_plc_type_env_var_selects_the_matching_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLC_TYPE", "modbus")
        monkeypatch.setenv("MODBUS_IP", "10.1.2.3")
        cfg = ConfigLoader(config_dir=CONFIG_DIR).get_plc_config()
        assert cfg["type"] == "modbus"
        assert (cfg["ip"], cfg["port"], cfg["unit_id"], cfg["tag"]) == ("10.1.2.3", 502, 1, "holding:0")
        assert "default_value" not in cfg  # the mock section is not merged in

    def test_type_without_a_section_yields_just_the_type(self, tmp_path: Path) -> None:
        (tmp_path / "plc.yaml").write_text("plc:\n  type: weird\n")
        assert ConfigLoader(config_dir=str(tmp_path)).get_plc_config() == {"type": "weird"}

    def test_no_plc_yaml_defaults_to_allen_bradley_type(self, tmp_path: Path) -> None:
        assert ConfigLoader(config_dir=str(tmp_path)).get_plc_config() == {"type": "allen_bradley"}


# ---------------------------------------------------------------------------
# get_auth_config()
# ---------------------------------------------------------------------------


class TestGetAuthConfig:
    def test_returns_dict_with_expected_keys(self, loader: ConfigLoader) -> None:
        cfg = loader.get_auth_config()
        assert isinstance(cfg, dict)
        # At minimum these safety-relevant keys must be present
        assert "secret_key" in cfg

    @pytest.mark.parametrize("raw", ["1234", "0123", "yes", "null", "[a]", "true", "false"])
    def test_password_is_always_a_string(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        """A password that happens to look numeric/boolean/null stays a str (SH-B1)."""
        monkeypatch.setenv("ADMIN_PASSWORD", raw)
        cfg = ConfigLoader(config_dir=CONFIG_DIR)
        result = cfg.get_auth_config()["admin_password"]
        assert result == raw
        assert isinstance(result, str)

    @pytest.mark.parametrize("raw", ["a: b", "x:", "'q", "[a", "p #c", "sqlite:///:memory:"])
    def test_yaml_special_characters_round_trip(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        """YAML-syntax-looking env values load without crashing and round-trip exactly (SH-B2)."""
        monkeypatch.setenv("ADMIN_PASSWORD", raw)
        cfg = ConfigLoader(config_dir=CONFIG_DIR)
        assert cfg.get_auth_config()["admin_password"] == raw

    def test_secret_key_containing_interpolation_syntax_is_not_silently_defaulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SECRET_KEY containing ``${`` must not resolve to the public default (SH-B3)."""
        monkeypatch.setenv("SECRET_KEY", "s3cr${et}")
        cfg = ConfigLoader(config_dir=CONFIG_DIR)
        assert cfg.get_auth_config()["secret_key"] == "s3cr${et}"

    def test_warning_names_an_existing_file(self, caplog: pytest.LogCaptureFixture) -> None:
        """warn_insecure_defaults() must point operators at a file that actually exists (SH-B3)."""
        import logging
        import re

        cfg = ConfigLoader(config_dir=CONFIG_DIR)
        with caplog.at_level(logging.WARNING, logger="shared.core.config_loader"):
            cfg.warn_insecure_defaults()
        messages = [r.getMessage() for r in caplog.records if "SECURITY" in r.getMessage()]
        assert messages, "capture is broken or the defaults are no longer insecure"
        named = {m for msg in messages for m in re.findall(r"[\w/.-]+\.yaml", msg)}
        assert named, messages
        for name in named:
            assert (Path(CONFIG_DIR) / Path(name).name).exists(), name


# ---------------------------------------------------------------------------
# _substitute_env_vars() / _quote_env_value() — SH-B1/B2/B3
# ---------------------------------------------------------------------------


class TestSubstituteEnvVars:
    @pytest.mark.parametrize(
        "raw,expected_type",
        [
            ("6379", int),
            ("0", int),
            ("-5", int),
            ("3.14", float),
            ("true", bool),
            ("false", bool),
            ("True", bool),
            ("yes", bool),
            ("no", bool),
        ],
    )
    def test_canonical_number_and_bool_keep_their_type(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected_type: type
    ) -> None:
        """Existing numeric/boolean env vars (e.g. ports, *_ENABLED flags) are unaffected."""
        from omegaconf import OmegaConf

        from shared.core.config_loader import _substitute_env_vars

        monkeypatch.setenv("X_SH_TEST", raw)
        import io

        content = "key: ${X_SH_TEST:default}\n"
        cfg = OmegaConf.load(io.StringIO(_substitute_env_vars(content)))
        value = OmegaConf.select(cfg, "key")
        assert isinstance(value, expected_type)

    def test_unset_env_var_keeps_default_text_unchanged(self) -> None:
        """The ``:default`` half of the syntax is untouched — only env-provided values are quoted."""
        from shared.core.config_loader import _substitute_env_vars

        assert _substitute_env_vars("key: ${TOTALLY_UNSET_SH_VAR:5}\n") == "key: 5\n"


# ---------------------------------------------------------------------------
# warn_insecure_defaults()
# ---------------------------------------------------------------------------


class TestWarnInsecureDefaults:
    _VARS = ("SECRET_KEY", "ADMIN_PASSWORD", "MAINTENANCE_PASSWORD")

    @staticmethod
    def _security_messages(cfg: ConfigLoader, caplog: pytest.LogCaptureFixture) -> list:
        import logging

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="shared.core.config_loader"):
            cfg.warn_insecure_defaults()
        return [r.getMessage() for r in caplog.records if "SECURITY" in r.getMessage()]

    @pytest.mark.parametrize(
        "var, role", [("ADMIN_PASSWORD", "admin"), ("MAINTENANCE_PASSWORD", "maintenance")]
    )
    @pytest.mark.parametrize("blank", ["", "  "])
    def test_blank_role_password_logs_an_error_naming_the_role(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        var: str,
        role: str,
        blank: str,
    ) -> None:
        """TS-1: fail closed at login, and say why at startup."""
        import logging

        for v in self._VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv(var, blank)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="shared.core.config_loader"):
            ConfigLoader(config_dir=CONFIG_DIR).warn_insecure_defaults()
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1 and f"{role} role" in errors[0], errors

    def test_non_blank_passwords_log_no_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        for v in self._VARS:
            monkeypatch.delenv(v, raising=False)
        with caplog.at_level(logging.WARNING, logger="shared.core.config_loader"):
            ConfigLoader(config_dir=CONFIG_DIR).warn_insecure_defaults()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_warns_once_per_credential_still_at_its_insecure_default(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        for var in self._VARS:
            monkeypatch.delenv(var, raising=False)
        messages = self._security_messages(ConfigLoader(config_dir=CONFIG_DIR), caplog)
        assert len(messages) == 3
        for key in ("auth.maintenance_password", "auth.admin_password", "auth.secret_key"):
            assert sum(key in m for m in messages) == 1, (key, messages)

    def test_no_warning_when_every_credential_is_overridden(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        for var in self._VARS:
            monkeypatch.delenv(var, raising=False)
        # positive control first: the same capture sees the warnings when the defaults are in place
        assert self._security_messages(ConfigLoader(config_dir=CONFIG_DIR), caplog)

        monkeypatch.setenv("SECRET_KEY", "a-very-long-secure-key-that-is-not-default")
        monkeypatch.setenv("ADMIN_PASSWORD", "s3cur3-admin-pw")
        monkeypatch.setenv("MAINTENANCE_PASSWORD", "s3cur3-maint-pw")
        assert self._security_messages(ConfigLoader(config_dir=CONFIG_DIR), caplog) == []

    def test_overriding_one_credential_silences_only_that_one(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        for var in self._VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("ADMIN_PASSWORD", "s3cur3-admin-pw")
        messages = self._security_messages(ConfigLoader(config_dir=CONFIG_DIR), caplog)
        assert len(messages) == 2 and not any("auth.admin_password" in m for m in messages)


class TestGetDatabaseUrlInMemory:
    def test_in_memory_sqlite_url_is_not_turned_into_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "database.yaml").write_text("database:\n  url: 'sqlite:///:memory:'\n")
        cfg = ConfigLoader(config_dir=str(tmp_path))
        assert cfg.get_database_url() == "sqlite:///:memory:"
