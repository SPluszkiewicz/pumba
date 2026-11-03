"""Code shared by more than one service.

Each service is still built into its own container; the Containerfiles copy
this directory alongside the service (`/app/services/common/` for `tools`,
`/app/common/` for the flat-layout `agent`), so nothing here may import from a
specific service.
"""
