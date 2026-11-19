"""``tep-shared`` declares exactly the third-party packages it imports, split into core and extras.

A library that installs only the core (``omegaconf`` + ``python-dotenv``) must be able to import every
module it uses; Flask, SQLAlchemy and Redis are extras (``[flask]``, ``[db]``, ``[redis]``). These tests
fail if a core module starts importing an extra's package at import time, if a package is imported but not
declared, or if a declared package is no longer used (``pillow`` sat unused in the hard dependencies).

"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Dict, Iterator, Set

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1]  # the repository root (holds pyproject.toml)
_PKG = _ROOT / "src" / "shared"  # the importable package
# import name -> distribution name
_DIST = {
    "omegaconf": "omegaconf",
    "dotenv": "python-dotenv",
    "flask": "flask",
    "sqlalchemy": "sqlalchemy",
    "redis": "redis",
}
_CORE = {"omegaconf", "python-dotenv"}
# The extra each non-core module belongs to. Every other module must import core packages only.
_MODULE_EXTRA = {
    "core/api_response.py": "flask",
    "core/auth.py": "flask",
    "core/db.py": "db",
    "camera_service.py": "redis",
}
_EXTRA_DISTS = {"flask": {"flask"}, "db": {"sqlalchemy"}, "redis": {"redis"}}


def _source_files() -> Iterator[Path]:
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_PKG).parts
        if rel[0] in {"tests", "build"} or any(p.endswith(".egg-info") for p in rel):
            continue
        yield path


def _top_level(path: Path, *, module_level_only: bool) -> Set[str]:
    """Distribution names of the third-party packages ``path`` imports (module level, or anywhere)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = tree.body if module_level_only else list(ast.walk(tree))
    found: Set[str] = set()
    for node in nodes:
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            if name in _DIST:
                found.add(_DIST[name])
            elif name not in sys.stdlib_module_names and name != "shared":
                raise AssertionError(
                    f"{path}: unmapped third-party import {name!r}: add it to _DIST and pyproject"
                )
    return found


def _declared() -> Dict[str, Set[str]]:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    def name(requirement: str) -> str:
        return re.split(r"[<>=!\[ ;]", requirement, maxsplit=1)[0].lower()

    declared = {"core": {name(r) for r in project["dependencies"]}}
    for extra in _EXTRA_DISTS:
        declared[extra] = {name(r) for r in project["optional-dependencies"][extra]}
    return declared


def test_core_dependencies_are_exactly_what_the_core_needs() -> None:
    assert _declared()["core"] == _CORE


@pytest.mark.parametrize("extra", sorted(_EXTRA_DISTS))
def test_each_extra_declares_its_package(extra: str) -> None:
    assert _declared()[extra] == _EXTRA_DISTS[extra]


def test_every_third_party_import_is_declared_and_every_declaration_is_used() -> None:
    declared = set().union(*_declared().values())
    imported = set().union(*(_top_level(p, module_level_only=False) for p in _source_files()))
    assert imported <= declared, f"imported but not declared: {sorted(imported - declared)}"
    assert declared <= imported, f"declared but never imported (drop it): {sorted(declared - imported)}"


@pytest.mark.parametrize("path", list(_source_files()), ids=lambda p: str(p.relative_to(_PKG)))
def test_module_level_imports_stay_inside_the_modules_extra(path: Path) -> None:
    allowed = set(_CORE)
    extra = _MODULE_EXTRA.get(str(path.relative_to(_PKG)))
    if extra:
        allowed |= _EXTRA_DISTS[extra]
    stray = _top_level(path, module_level_only=True) - allowed
    where = path.relative_to(_PKG)
    assert not stray, f"{where} imports {sorted(stray)} at import time (core modules: core deps only)"


def test_core_modules_import_with_flask_sqlalchemy_and_redis_absent() -> None:
    """What a library that installed only ``tep-shared`` (no extras) can actually import."""
    code = (
        "import sys\n"
        "for name in ('flask', 'sqlalchemy', 'redis', 'PIL'):\n"
        "    sys.modules[name] = None  # makes `import name` raise ImportError\n"
        "import shared.core.config_loader, shared.core.log_setup, shared.core.reconnect\n"
        "import shared.core.camera_stream, shared.core.envelope, shared.core.http_utils\n"
        "import shared.core.paths, shared.core.image_paths\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd="/",
        env={"PYTHONPATH": str(_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr
