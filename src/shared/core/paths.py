"""Project-root resolution for the modules in ``shared.core``.

``ConfigLoader`` and ``log_setup`` anchor the config directory, relative sqlite/storage paths and the log
directory to a *project root*. That root belongs to the consuming project, not to wherever ``shared`` happens
to be installed, so it is resolved in this one place, in this order:

1. an explicit ``root`` argument;
2. the ``TEP_PROJECT_ROOT`` environment variable;
3. development fallback: when ``shared`` is an editable install from a checkout that has a ``config/``
   directory at its top level (``<checkout>/src/shared/core/paths.py``), that checkout;
4. otherwise :class:`ProjectRootError`. It never falls back to the current directory.

The fallback depends on where the package is installed, never on the cwd. Keep the depth in this one place.
"""

import os
from pathlib import Path
from typing import Optional, Union

ENV_PROJECT_ROOT = "TEP_PROJECT_ROOT"

# <checkout>/src/shared/core/paths.py  →  core/  →  shared/  →  src/  →  <checkout>
_CHECKOUT_ROOT: Path = Path(__file__).resolve().parents[3]


class ProjectRootError(RuntimeError):
    """No project root was given and none can be inferred safely."""


def _dev_checkout_root() -> Optional[Path]:
    if "site-packages" in _CHECKOUT_ROOT.parts or not (_CHECKOUT_ROOT / "config").is_dir():
        return None
    return _CHECKOUT_ROOT


def resolve_project_root(root: Union[str, "os.PathLike[str]", None] = None) -> Path:
    """Return the absolute project root, or raise :class:`ProjectRootError` (see the module docstring)."""
    if root is not None:
        return Path(root).resolve()
    from_env = os.environ.get(ENV_PROJECT_ROOT)
    if from_env:
        return Path(from_env).resolve()
    checkout = _dev_checkout_root()
    if checkout is not None:
        return checkout
    raise ProjectRootError(
        f"No project root: pass root=... or set {ENV_PROJECT_ROOT} to the directory that holds your "
        "config/ and data/ (shared is installed as a package here, so it cannot infer one)."
    )
