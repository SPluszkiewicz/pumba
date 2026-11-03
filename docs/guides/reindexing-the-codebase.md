# Reindexing the codebase

The agent answers questions using whatever was last indexed into Qdrant -
it does not read the mounted project live on every request. Re-run the
indexer any time the indexed source actually changes and you want the
agent's retrieval to reflect it:

```bash
python scripts/deploy.py index
# or: podman compose --profile index run --rm indexer
```

## Several checkouts

Each repository is one Qdrant collection, named from the remote URL. Two
tickets of that repository share the collection. Two remotes are two
collections. The registry is `state/workspaces.json` in this tool repo.
Qdrant data stays in the `qdrant-data` volume.

```bash
python scripts/deploy.py workspace d:/svn/ticket-142
python scripts/deploy.py workspace d:/svn/ticket-143
python scripts/deploy.py workspace --update d:/svn/ticket-142
```

The first command indexes `ticket-142` once. The second only records the
other path and mounts it beside the first (`/checkouts/<id>` in `tools`
and `agent`). Adding a mount recreates those two containers. Switching
between copies that are already mounted does not. `--update` re-embeds
files whose content hash changed, into the shared collection, and does
not copy uncommitted edits into it.

A question carries the checkout path (`X-Workspace` in that folder's
`.vscode/mcp.json`, or `"workspace"` on `POST /chat`). The agent searches
that repository's collection, runs tools on that checkout, and adds
`svn status` or `git status` for uncommitted files. An unregistered path
is refused. The MCP URL stays `http://127.0.0.1:8092/mcp`.

One indexer runs at a time. A question on a repository that is already
indexed does not wait for a first index of a different remote, except
that both share the CPU embedder if a job is already running.

## The default tree

`CODEBASE_PATH` (default `./sample`, the calculator fixture) is the tree
used when a question does not name a checkout. `app/` is the `llm-client`
and stays out of that mount. `python scripts/deploy.py index` recreates
that mount and re-indexes `code_chunks`.

Register a real checkout with `workspace` instead of rewriting
`CODEBASE_PATH`. That command does not change `.env`. Drive paths such as
`d:/svn/ticket-142` are mapped to the form the container engine needs.

The default tree (`CODEBASE_PATH`) is mounted at `/workspace/app`. A
registered checkout is also mounted at `/checkouts/<id>`, and a question
that names it uses that path. The one-shot indexer for a checkout still
sees that tree at `/workspace/app` for the duration of the job.

The indexer parses C++ **and** CMake / Markdown / YAML / JSON / TOML /
text (see [The indexing pipeline](../concepts/indexing-pipeline.md)). A
Python or TypeScript repo still indexes almost empty.

File writes back into the mounted tree are **not** implemented yet - the
mount stays read-only (`:ro`).

## When you need to do this

- After editing, adding, or removing files that should enter the shared index (`workspace --update`, or `index` for the default tree).
- After changing `CODEBASE_PATH` in `.env` (default tree only).
- After the very first `podman compose up` (there's nothing in Qdrant yet -
  see [First run](../getting-started/first-run.md)).
- You do **not** need to re-index just because you restarted the stack -
  Qdrant's data lives in the `qdrant-data` named volume and survives
  `podman compose down`/`up` cycles.

## Is it safe to run repeatedly?

Yes. Unchanged files are skipped when their `git_blob_hash` still matches
Qdrant (no embedder call). Files that disappeared from the walk are
deleted (tombstoned) at the end of the batch; the JSON summary line
reports `files_skipped` and `files_deleted`. Point IDs stay a
deterministic function of `(file_path, symbol_name, start_line)`, so a
changed file updates in place after its old points are dropped.

## Watch mode

`indexer` also supports `--watch` for continuous re-indexing on file
changes, but it's best-effort and Linux-preferred - see
[The indexing pipeline](../concepts/indexing-pipeline.md) for why (inotify
doesn't propagate reliably through a Windows host -> Podman machine -> WSL2
bind mount). The one-shot command above is the primary supported workflow;
reach for `--watch` only on a native Linux host.
