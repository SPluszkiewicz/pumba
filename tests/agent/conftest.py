"""Shared pytest setup for agent service tests.

`services/agent` uses bare top-level imports (`import rag`, `from
llm_client import ...`, etc.) since that's how the Containerfile lays the
module out (`/app/main.py`, `/app/rag.py`, ...). This puts that directory on
`sys.path` so the tests can import those modules directly without needing
`services/agent` to be an installed package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[2] / "services"
AGENT_SRC = SERVICES / "agent"
for path in (AGENT_SRC, SERVICES):
    # `services/` itself so `common.tool_models` resolves the way it does in
    # the container (`/app/common/`, copied next to the agent modules).
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("AGENT_BEARER_TOKEN", "test-token")
os.environ.setdefault("AGENT_TOOL_LOOP_MAX_ITERATIONS", "4")
os.environ.setdefault("LLM_BASE_URL", "http://llm-test:8080")
os.environ.setdefault("EMBEDDER_BASE_URL", "http://embedder-test:8081")
os.environ.setdefault("QDRANT_URL", "http://qdrant-test:6333")
os.environ.setdefault("QDRANT_COLLECTION", "code_chunks")
os.environ.setdefault("TOOLS_BASE_URL", "http://tools-test:8091")
# No SDK retries in tests: the "LLM unreachable" scenarios would otherwise
# sleep through the openai client's exponential backoff.
os.environ.setdefault("AGENT_LLM_MAX_RETRIES", "0")

# Flat-layout name clash: services/indexer also has a `main.py`, and the
# indexer tests put their directory on sys.path too. Bind `main` explicitly
# to this service's file so `import main` in tests/agent always resolves to
# the agent app, whatever order the test directories are given on the
# command line. (The indexer tests never import their `main`.)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("main", AGENT_SRC / "main.py")
assert _spec is not None and _spec.loader is not None
_agent_main = importlib.util.module_from_spec(_spec)
sys.modules["main"] = _agent_main
_spec.loader.exec_module(_agent_main)
