"""ConfigLoader's documented priority chain, tested with conflicting values at every layer.

    environment variable  >  .env file  >  YAML file value  >  default written in the YAML

The root conftest turns ``dotenv.load_dotenv`` into a no-op for the whole session (so a developer's real
``.env`` can't leak in), which meant the ``.env`` tier could never be tested and nothing checked that the
chain's *order* survived a refactor. These tests use a temporary config directory and a temporary ``.env``
and route ``ConfigLoader``'s own ``load_dotenv()`` call to the real dotenv implementation pointed at it.

Two properties the docs only imply are pinned too: an environment variable only reaches keys whose YAML
uses a ``${VAR:default}`` placeholder (there is no generic env override), and when two YAML files define the
same key the later one in the loader's fixed file order wins.
"""

from pathlib import Path
from typing import Any, Optional

import pytest
from dotenv.main import load_dotenv as real_load_dotenv

from shared.core import config_loader
from shared.core.config_loader import ConfigError, ConfigLoader

pytestmark = pytest.mark.unit


def _write(config_dir: Path, name: str, text: str) -> None:
    (config_dir / name).write_text(text, encoding="utf-8")


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    return d


@pytest.fixture()
def dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make ``ConfigLoader()`` read this ``.env`` with the REAL python-dotenv (default ``override=False``)."""
    path = tmp_path / ".env"
    path.write_text("")
    monkeypatch.setattr(config_loader, "load_dotenv", lambda *a, **kw: real_load_dotenv(path, *a, **kw))
    return path


def _loader(config_dir: Path) -> ConfigLoader:
    return ConfigLoader(config_dir=str(config_dir))


# ---------------------------------------------------------------------------
# the chain: env > .env > YAML default
# ---------------------------------------------------------------------------


@pytest.fixture()
def services_yaml(config_dir: Path) -> Path:
    _write(
        config_dir,
        "services.yaml",
        "svc:\n  port: ${PREC_PORT:1111}\n  name: ${PREC_NAME:from-yaml-default}\n",
    )
    return config_dir


def test_yaml_default_is_used_when_nothing_else_is_set(services_yaml: Path, dotenv_file: Path) -> None:
    cfg = _loader(services_yaml)
    assert cfg.get("svc.port") == 1111 and cfg.get("svc.name") == "from-yaml-default"


def test_dotenv_beats_the_yaml_default(services_yaml: Path, dotenv_file: Path) -> None:
    dotenv_file.write_text("PREC_PORT=2222\nPREC_NAME=from-dotenv\n")
    cfg = _loader(services_yaml)
    assert cfg.get("svc.port") == 2222 and cfg.get("svc.name") == "from-dotenv"


def test_environment_beats_dotenv_and_the_yaml_default(
    services_yaml: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_file.write_text("PREC_PORT=2222\nPREC_NAME=from-dotenv\n")
    monkeypatch.setenv("PREC_PORT", "3333")  # only one key is set in the environment
    cfg = _loader(services_yaml)
    assert cfg.get("svc.port") == 3333  # env wins over .env
    assert cfg.get("svc.name") == "from-dotenv"  # .env still wins where the env is silent


def test_each_layer_is_independent_per_key(
    config_dir: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        config_dir,
        "services.yaml",
        "a: ${PREC_A:yaml-a}\nb: ${PREC_B:yaml-b}\nc: ${PREC_C:yaml-c}\nd: ${PREC_D:yaml-d}\n",
    )
    dotenv_file.write_text("PREC_B=dotenv-b\nPREC_C=dotenv-c\n")
    monkeypatch.setenv("PREC_C", "env-c")
    monkeypatch.setenv("PREC_D", "env-d")
    cfg = _loader(config_dir)
    assert [cfg.get(k) for k in "abcd"] == ["yaml-a", "dotenv-b", "env-c", "env-d"]


def test_changing_env_after_construction_is_picked_up_only_on_reload(
    services_yaml: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _loader(services_yaml)
    assert cfg.get("svc.port") == 1111
    monkeypatch.setenv("PREC_PORT", "4444")
    assert cfg.get("svc.port") == 1111  # cached
    cfg.reload()
    assert cfg.get("svc.port") == 4444


def test_reload_rereads_the_yaml_files(config_dir: Path, dotenv_file: Path) -> None:
    _write(config_dir, "services.yaml", "k: one\n")
    cfg = _loader(config_dir)
    assert cfg.get("k") == "one"
    _write(config_dir, "services.yaml", "k: two\n")
    assert cfg.get("k") == "one"
    cfg.reload()
    assert cfg.get("k") == "two"


# ---------------------------------------------------------------------------
# only placeholder keys can be overridden
# ---------------------------------------------------------------------------


def test_a_yaml_value_without_a_placeholder_ignores_env_and_dotenv(
    config_dir: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no generic ``SECTION_KEY`` env override: documenting a variable that has no ``${...}`` in the
    YAML does nothing, which is easy to get wrong when adding a setting."""
    _write(config_dir, "services.yaml", "svc:\n  retries: 3\n")
    dotenv_file.write_text("SVC_RETRIES=9\nRETRIES=9\n")
    monkeypatch.setenv("SVC_RETRIES", "7")
    assert _loader(config_dir).get("svc.retries") == 3


def test_placeholder_defaults_are_taken_literally_including_colons_and_urls(
    config_dir: Path, dotenv_file: Path
) -> None:
    _write(config_dir, "services.yaml", "u: ${PREC_URL:http://localhost:8081/x}\nt: ${PREC_TAG:holding:0}\n")
    cfg = _loader(config_dir)
    assert cfg.get("u") == "http://localhost:8081/x" and cfg.get("t") == "holding:0"


def test_an_empty_default_resolves_to_empty_and_an_empty_env_var_overrides_the_default(
    config_dir: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(config_dir, "redis.yaml", "redis:\n  password: ${PREC_PW:}\n  host: ${PREC_HOST:localhost}\n")
    monkeypatch.setenv("PREC_HOST", "")
    cfg = _loader(config_dir)
    assert cfg.get_redis_config()["password"] is None
    assert cfg.get("redis.host") == ""  # a set-but-empty variable wins over the default (it is "set")


# ---------------------------------------------------------------------------
# merging several YAML files
# ---------------------------------------------------------------------------


def test_later_files_override_earlier_ones_in_the_loaders_fixed_order(
    config_dir: Path, dotenv_file: Path
) -> None:
    """Order is database, redis, services, plc, storage, pipeline: not alphabetical, not filesystem order."""
    _write(config_dir, "pipeline.yaml", "shared_key: from-pipeline\nonly_pipeline: p\n")
    _write(config_dir, "database.yaml", "shared_key: from-database\nonly_database: d\n")
    _write(config_dir, "services.yaml", "shared_key: from-services\n")
    cfg = _loader(config_dir)
    assert cfg.get("shared_key") == "from-pipeline"
    assert (cfg.get("only_database"), cfg.get("only_pipeline")) == ("d", "p")


def test_merge_is_deep_not_replace(config_dir: Path, dotenv_file: Path) -> None:
    _write(config_dir, "services.yaml", "svc:\n  a: 1\n  b: 2\n")
    _write(config_dir, "pipeline.yaml", "svc:\n  b: 20\n  c: 30\n")
    cfg = _loader(config_dir)
    assert [cfg.get("svc.a"), cfg.get("svc.b"), cfg.get("svc.c")] == [1, 20, 30]


def test_unknown_yaml_files_in_the_directory_are_ignored(config_dir: Path, dotenv_file: Path) -> None:
    _write(config_dir, "services.yaml", "k: real\n")
    _write(config_dir, "extra.yaml", "k: ignored\nother: x\n")
    cfg = _loader(config_dir)
    assert cfg.get("k") == "real" and cfg.get("other") is None


def test_missing_directory_or_files_give_an_empty_config_and_defaults(
    tmp_path: Path, dotenv_file: Path
) -> None:
    cfg = _loader(tmp_path / "does-not-exist")
    assert cfg.get("anything") is None and cfg.get("anything", 5) == 5
    assert cfg.get_redis_config()["host"] == "localhost" and cfg.get_redis_config()["port"] == 6379


# ---------------------------------------------------------------------------
# get(): a missing key gives the default; a key that exists but cannot be resolved raises
# (D-SH2: a silent fallback hid SH-B3, a downgrade to a public key)
# ---------------------------------------------------------------------------


def test_a_broken_interpolation_raises_config_error_naming_the_key(
    config_dir: Path, dotenv_file: Path
) -> None:
    _write(config_dir, "services.yaml", "a: ${missing_key}\nok: 1\n")
    cfg = _loader(config_dir)
    with pytest.raises(ConfigError, match="'a'"):
        cfg.get("a", "fallback")
    assert cfg.get("ok") == 1  # one bad key does not poison the rest
    assert cfg.get("not.there", "fallback") == "fallback"  # missing keys still default


# ---------------------------------------------------------------------------
# auth values bypass YAML: env > .env > YAML, stringly typed
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_yaml(config_dir: Path) -> Path:
    _write(
        config_dir,
        "services.yaml",
        "auth:\n  admin_password: ${ADMIN_PASSWORD:yaml-admin}\n  maintenance_password: 12345\n"
        "  secret_key: ${SECRET_KEY:yaml-secret}\n",
    )
    return config_dir


@pytest.fixture(autouse=True)
def _no_ambient_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ADMIN_PASSWORD", "MAINTENANCE_PASSWORD", "SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_auth_values_follow_env_then_dotenv_then_yaml(
    auth_yaml: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _loader(auth_yaml).get_auth_config()["admin_password"] == "yaml-admin"
    dotenv_file.write_text("ADMIN_PASSWORD=dotenv-admin\nSECRET_KEY=dotenv-secret\n")
    monkeypatch.setenv("SECRET_KEY", "env-secret")
    cfg = _loader(auth_yaml).get_auth_config()
    assert cfg["admin_password"] == "dotenv-admin" and cfg["secret_key"] == "env-secret"


@pytest.mark.parametrize("key", ["admin_password", "maintenance_password", "secret_key"])
def test_a_numeric_yaml_credential_is_returned_as_a_string(
    config_dir: Path, dotenv_file: Path, key: str
) -> None:
    """An unquoted ``admin_password: 12345`` parses as an int; a login compares strings."""
    full = {"admin_password": "x", "maintenance_password": "y", "secret_key": "z", key: "12345"}
    block = "".join(f"  {k}: {'12345' if k == key else v}\n" for k, v in full.items())
    _write(config_dir, "services.yaml", f"auth:\n{block}")
    value: Optional[Any] = _loader(config_dir).get_auth_config()[key]  # type: ignore[literal-required]
    assert value == "12345" and isinstance(value, str)


def test_missing_auth_block_raises_instead_of_using_a_public_default(
    config_dir: Path, dotenv_file: Path
) -> None:
    """D-PK10: the public library has no built-in credentials; an unconfigured one must fail loudly."""
    from shared.core.config_loader import ConfigError

    with pytest.raises(ConfigError, match="auth.admin_password|auth.maintenance_password|auth.secret_key"):
        _loader(config_dir).get_auth_config()


def test_env_alone_is_enough_when_the_yaml_has_no_auth_block(
    config_dir: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var, value in (("ADMIN_PASSWORD", "a"), ("MAINTENANCE_PASSWORD", "m"), ("SECRET_KEY", "k")):
        monkeypatch.setenv(var, value)
    assert _loader(config_dir).get_auth_config() == {
        "maintenance_password": "m",
        "admin_password": "a",
        "secret_key": "k",
    }


# ---------------------------------------------------------------------------
# storage paths
# ---------------------------------------------------------------------------


def test_absolute_storage_path_from_the_environment_is_used_as_is_and_created(
    config_dir: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "mnt" / "frames"
    _write(
        config_dir,
        "storage.yaml",
        "storage:\n  images:\n    camera_frames: ${CAMERA_FRAMES_DIR:data/images/camera_frames}\n",
    )
    monkeypatch.setenv("CAMERA_FRAMES_DIR", str(target))
    path = _loader(config_dir).get_storage_path("images.camera_frames")
    assert path == target and target.is_dir()


def test_relative_storage_path_is_anchored_at_the_repo_root_not_the_cwd(
    config_dir: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from shared.core.paths import resolve_project_root

    _write(config_dir, "storage.yaml", "storage:\n  models:\n    path: data/_prec_test_models\n")
    monkeypatch.chdir(tmp_path)
    path = _loader(config_dir).get_storage_path("models.path")
    try:
        assert path == (resolve_project_root() / "data" / "_prec_test_models").resolve()
    finally:
        if path.exists():
            path.rmdir()


def test_unconfigured_storage_key_falls_back_to_a_data_subdirectory_named_after_the_key(
    config_dir: Path, dotenv_file: Path
) -> None:
    from shared.core.paths import resolve_project_root

    path = _loader(config_dir).get_storage_path("zzz_prec_test.sub")
    try:
        assert path == (resolve_project_root() / "data" / "zzz_prec_test" / "sub").resolve()
    finally:
        for p in (path, path.parent):
            if p.exists():
                p.rmdir()


# ---------------------------------------------------------------------------
# COMPAT(D-E4-5): services.pipeline_maker was renamed services.pipeline_editor
# ---------------------------------------------------------------------------


def test_service_key_prefers_the_new_name(config_dir: Path, dotenv_file: Path) -> None:
    _write(config_dir, "services.yaml", "services:\n  pipeline_editor:\n    url: http://new:1\n")
    assert _loader(config_dir).get_service_url("pipeline_editor") == "http://new:1"


def test_service_key_falls_back_to_the_legacy_section_with_a_warning(
    config_dir: Path, dotenv_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(config_dir, "services.yaml", "services:\n  pipeline_maker:\n    url: http://old:2\n")
    cfg = _loader(config_dir)
    with caplog.at_level("WARNING", logger="shared.core.config_loader"):
        assert cfg.service_key("pipeline_editor") == "pipeline_maker"
        assert cfg.get_service_url("pipeline_editor") == "http://old:2"
    assert any("deprecated" in r.getMessage() for r in caplog.records)


def test_service_key_is_the_identity_for_other_services(config_dir: Path, dotenv_file: Path) -> None:
    assert _loader(config_dir).service_key("tep_ai") == "tep_ai"


def test_legacy_pipeline_maker_env_vars_are_still_honoured(
    config_dir: Path, dotenv_file: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _write(config_dir, "services.yaml", "services:\n  pipeline_editor:\n    port: ${PIPELINE_EDITOR_PORT:5005}\n")
    monkeypatch.delenv("PIPELINE_EDITOR_PORT", raising=False)
    monkeypatch.setenv("PIPELINE_MAKER_PORT", "6001")
    with caplog.at_level("WARNING", logger="shared.core.config_loader"):
        assert _loader(config_dir).get("services.pipeline_editor.port") == 6001
    assert any("PIPELINE_MAKER_PORT is deprecated" in r.getMessage() for r in caplog.records)
    monkeypatch.setenv("PIPELINE_EDITOR_PORT", "7002")  # the new name wins
    assert _loader(config_dir).get("services.pipeline_editor.port") == 7002
