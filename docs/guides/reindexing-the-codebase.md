# Reindexing the codebase

The agent answers questions using whatever was last indexed into Qdrant.
It does not read the mounted project live on every request. Re-run the
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

The first command embeds `ticket-142` once into the repository collection
and tags its points with that checkout's ID (`co_<hash>`). The second
command walks `ticket-143` (even on another branch): files whose
`git_blob_hash` already exists in the collection simply get `ticket-143`'s
checkout ID attached to their `checkouts` payload without re-embedding, and
only files that differ on that branch are embedded. Adding a mount recreates
`tools`, `agent`, and `ui-proxy` (`index` does the same). `--update`
refreshes the active checkout's blob links and embeds any newly changed files.

!!! warning "`serena` keeps its old mounts"
    `deploy.py` does not recreate `serena`, although `state/compose.workspaces.yaml`
    adds the new checkout to its mounts and `CODEBASE_PATH` is mounted into it
    too. A container only gets a mount when it is created, so after `workspace`
    on a new path, or after changing `CODEBASE_PATH`, recreate it by hand with
    the same files `deploy.py` stacks (Podman shown; on Docker use
    `config/compose/docker.yaml` instead of `podman.yaml`):

    ```bash
    podman compose -f compose.yaml -f config/compose/podman.yaml \
      -f state/compose.workspaces.yaml up -d --force-recreate --no-deps serena
    ```

A question carries the checkout path (`X-Workspace` in that folder's
`.vscode/mcp.json`, or `"workspace"` on `POST /chat`). The agent filters
that repository's Qdrant collection by `checkouts` (`co_<hash>`), runs tools
on that checkout, and adds `svn status` or `git status` for uncommitted
files. An unregistered path is refused. The MCP URL stays
`http://127.0.0.1:8092/mcp`.

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

The indexer, `agent`, and `tools` mount the tree read-only (`:ro`). The
agent changes files through `serena`'s write tools, the only read-write
checkout mount: that never commits, and those edits are not in the index
until you re-run `index` or `workspace --update`. In the meantime, on a git
or svn work tree, the agent reads uncommitted files live and puts them in
front of the retrieved chunks.

## When you need to do this

- After editing, adding, or removing files that should enter the shared index (`workspace --update`, or `index` for the default tree).
- After changing `CODEBASE_PATH` in `.env` (default tree only).
- After the very first raw `podman compose up` (there's nothing in Qdrant
  yet; `python scripts/deploy.py up` already runs the indexer. See
  [First run](../getting-started/first-run.md)).
- After `python scripts/deploy.py down --volumes`, which removes `qdrant-data`.
- You do **not** need to re-index just because you restarted the stack.
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
changes: it runs the same full batch first, then listens with a 0.5s
debounce. It is best-effort and Linux-preferred. See
[The indexing pipeline](../concepts/indexing-pipeline.md) for why (inotify
doesn't propagate reliably through a Windows host -> Podman machine -> WSL2
bind mount). The one-shot command above is the primary supported workflow;
reach for `--watch` only on a native Linux host.
