#!/usr/bin/env python3
"""The only host command. Implementation: scripts/host/deploy.py.

    python scripts/deploy.py up
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_HOST = Path(__file__).resolve().parent / "host"
_CONTAINER = _HOST.parent / "container"
sys.path.insert(0, str(_HOST))
if str(_CONTAINER) not in sys.path:
    sys.path.insert(1, str(_CONTAINER))

if __name__ == "__main__":
    runpy.run_path(str(_HOST / "deploy.py"), run_name="__main__")
