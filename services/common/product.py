"""Product identity. Runtime names live here; prose in docs can still say them.

A rename of the stack is this file, `compose.yaml` `name:` (Compose cannot
import Python), and the Grafana dashboard filename/uid if you keep those
equal to `SLUG`. `tests/scripts/test_product_identity.py` fails when those
drift. The WSL distro is the Podman VM on the operator's machine, not the
repository name.
"""

from __future__ import annotations

NAME = "Pumba"
SLUG = "pumba"
MCP_SERVER = "pumba"
MCP_LEGACY = ("local-llm",)
COMPOSE_PROJECT = "pumba"
ARCHIVE_ROOT = "pumba"
WSL_DISTRO = "local-llm"
