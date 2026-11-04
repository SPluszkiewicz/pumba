"""Put `services/mcp` on sys.path the way the Containerfile lays it out."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
MCP_SRC = _REPO / "services" / "mcp"
COMMON_SRC = _REPO / "services" / "common"
for _path in (MCP_SRC, COMMON_SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

os.environ.setdefault("AGENT_BEARER_TOKEN", "test-token")
os.environ.setdefault("AGENT_BASE_URL", "http://agent-test:8090")
os.environ.setdefault("MCP_REQUEST_TIMEOUT_SECONDS", "5")
