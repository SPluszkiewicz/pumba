"""Shared pytest setup for tests of the host-facing `scripts/` utilities.

`scripts/*.py` are plain top-level scripts (not an installed package), so
this puts `scripts/` on `sys.path` to let tests import them directly, the
same pattern used by `tests/agent/conftest.py` for `services/agent`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
for _subdir in ("container", "host"):
    _path = str(_SCRIPTS / _subdir)
    if _path not in sys.path:
        sys.path.insert(0, _path)
