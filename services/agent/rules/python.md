---
globs:
  - "**/*.py"
  - "**/*.pyi"
  - "**/pyproject.toml"
  - "**/requirements*.txt"
---
Python (PEP 8 and PEP 484):

- Type hints on public functions. `pathlib.Path` instead of hand-built path strings. No bare `except:`.
- Context managers for files and locks. No mutable default arguments.
- Formatting and lint match the project's tool (Ruff if `pyproject.toml` selects it). Do not add a second formatter.
