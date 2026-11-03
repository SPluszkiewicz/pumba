"""Put `services/mcp` on sys.path the way the Containerfile lays it out."""

from __future__ import annotations

import os
import sys
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[2] / "services" / "mcp"
if str(MCP_SRC) not in sys.path:
    sys.path.insert(0, str(MCP_SRC))

os.environ.setdefault("AGENT_BEARER_TOKEN", "test-token")
os.environ.setdefault("AGENT_BASE_URL", "http://agent-test:8090")
os.environ.setdefault("MCP_REQUEST_TIMEOUT_SECONDS", "5")
