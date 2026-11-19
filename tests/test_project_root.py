"""D-PK6 / D-PK11: the project root is explicit, env-provided, or a dev checkout — never the cwd."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.core import paths
from shared.core.config_loader import ConfigLoader
from shared.core.paths import ENV_PROJECT_ROOT, ProjectRootError, resolve_project_root

pytestmark = pytest.mark.unit


def test_explicit_root_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROJECT_ROOT, str(tmp_path / "env"))
    assert resolve_project_root(tmp_path / "arg") == (tmp_path / "arg").resolve()


def test_env_var_is_used_when_no_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROJECT_ROOT, str(tmp_path))
    assert resolve_project_root() == tmp_path.resolve()


def test_installed_package_without_root_raises_and_never_uses_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates ``shared`` installed in site-packages: no env, no argument -> error, not the cwd."""
    monkeypatch.delenv(ENV_PROJECT_ROOT, raising=False)
    monkeypatch.setattr(paths, "_CHECKOUT_ROOT", tmp_path / "site-packages" / "x")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ProjectRootError, match=ENV_PROJECT_ROOT):
        resolve_project_root()


def test_config_loader_anchors_relative_paths_to_its_root(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    cfg = ConfigLoader(root=str(tmp_path))
    assert cfg.config_dir == tmp_path.resolve() / "config"
    assert cfg.get_storage_path("images.x") == (tmp_path / "data" / "images" / "x").resolve()


def test_config_loader_with_explicit_config_dir_only_needs_a_root_when_a_relative_path_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_PROJECT_ROOT, raising=False)
    monkeypatch.setattr(paths, "_CHECKOUT_ROOT", tmp_path / "site-packages" / "x")
    cfg = ConfigLoader(config_dir=str(tmp_path))  # no root needed yet
    assert cfg.get("anything", "d") == "d"
    with pytest.raises(ProjectRootError):
        cfg.get_storage_path("images.x")
