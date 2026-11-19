"""Every ``shared.core`` module imports without pulling in tables, migrations or other modules.

This walks the whole package so a new core module cannot quietly import table definitions, migrations or
application code.
"""

import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import shared.core

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_MODULES = sorted(m.name for m in pkgutil.iter_modules(shared.core.__path__))


def test_core_module_discovery_is_not_empty() -> None:
    assert {"db", "config_loader", "paths"} <= set(CORE_MODULES)


@pytest.mark.parametrize("name", CORE_MODULES)
def test_core_module_is_table_free(name: str) -> None:
    code = (
        f"import sys, shared.core.{name}\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('full_tep', 'pipeline_maker', 'tep_ai', 'anomaly_detector_ABC', 'dataset_service')]\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, cwd="/")
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
