"""Lay out `services/indexer` and `services/` the way the indexer Containerfile does."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _path in (_REPO / "services" / "indexer", _REPO / "services"):
    _as_str = str(_path)
    if _as_str not in sys.path:
        sys.path.insert(0, _as_str)
