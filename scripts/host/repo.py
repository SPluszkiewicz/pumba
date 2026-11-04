"""Repo root and `services/common` on `sys.path`.

Host scripts import this first so `import product` resolves to
`services/common/product.py` without each file repeating the path dance.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_COMMON = ROOT / "services" / "common"
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))
