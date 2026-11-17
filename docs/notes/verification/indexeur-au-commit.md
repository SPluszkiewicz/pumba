# Hooking the indexer to a commit

> **Status**: informational, no decision. The repository does not hook the indexer to a commit: `index` and `workspace --update` run the entire batch, and the only hook shipped (`post-commit.train.sample`) passes `--skip-index`. Written on 2026-11-10. · **Last verified**: 2026-11-12

Research note of 2026-11-10. The guides describe how to use the stack. The code, Compose and the contracts own the behavior. When a guide and the code diverge, both are cited separately.

The subject is the `indexer` service of this repository: tree walk, chunking, embeddings, Qdrant upsert. The operator path is `python scripts/deploy.py index` and `python scripts/deploy.py workspace`. This is not Cursor's indexing. The hook `scripts/git-hooks/post-commit.train.sample` concerns training, and it passes `--skip-index`.

## Question

How can this indexer be hooked so that it refreshes on every commit, how can only what was committed be re-indexed, and is it a good idea to re-index only that?

## Short answer

The second pass still walks the whole retained tree, re-reads every file and recomputes its fingerprint. A fingerprint already linked to this checkout stops before the parse, the embedder and the upsert. The Qdrant snapshot happens anyway (payloads, without vectors, pages of 256). The repository gives no duration.

No command accepts a list of paths. `index` and `workspace --update` run the batch on the entire tree. `process_file` and `apply_watch_event` handle one path. The tombstone of a vanished file is the remainder of that walk, or `delete_by_file_paths`. A `process_file` that cannot read the file returns `empty` and detaches nothing.

The indexer hashes the bytes on disk, not the commit tree. For uncommitted changes, the agent runs `git status --porcelain=v1 -u` (names and statuses, not the diff) and re-reads at most six C or C++ sources. `post-commit` receives no file. With a single commit, `git diff-tree` compares that commit with its parents. The example hook, installed in the `CODEBASE_PATH` repository, calls `train --incremental --skip-index`.

`deploy.py index` on every commit would recreate `tools`, `patch`, `agent` and `ui-proxy` (not `serena`, which nevertheless mounts the same copy), then index `CODEBASE_PATH`, without `IndexLock`. `workspace --update <checkout>` takes the lock and indexes that tree into its collection. A list limited to the commit would avoid walking the other files. Without the old paths, it would leave their links behind. The repository runs the entire batch and does not choose for the operator.

## The second pass still walks the whole tree

`index_source_root` says it, then does it:

> Unchanged files (same `git_blob_hash` as already stored) are skipped.
> Blobs already embedded by another branch/checkout are linked without
> re-embedding. Files present for this checkout in the collection but
> absent from this walk are detached/deleted at the end.

The order in `services/indexer/main.py` is fixed. `snapshot()` first. Then `for path in chunker.iter_source_files(root)`. Each file calls `process_file` and the loop waits for its return. `main` calls `index_source_root` before `_run_watch`. The module docstring says that `--watch` "runs the same full batch first".

`snapshot` (`services/indexer/vector_store.py`) scrolls the whole collection if it exists. `with_vectors` is false. The payload keys requested are `file_path`, `git_blob_hash`, `checkouts`. The page is 256 points (`_scroll`, `limit: 256`). The result is `checkout_hashes`, the shared blobs, and the list of paths already linked to `INDEXER_CHECKOUT_ID`. `tests/indexer/test_index_incremental.py`, `test_shared_tree_attaches_in_one_call_without_embedding`, locks in a single `snapshot` and a single `attach_checkouts` for twelve files already present. The test counts the scrolls and the attaches. The loop over `iter_source_files` stays in `index_source_root`.

`iter_source_files` (`services/indexer/chunker.py`) walks the tree with `iterdir`, ignores `SKIPPED_DIR_NAMES` and the `cmake-build-*` names, keeps what `is_source_file` accepts, and sorts. No call to `git ls-files`. An untracked file whose extension is retained is in the walk.

For each file, `process_file` starts with `path.read_bytes()`, then `chunker.compute_blob_hash`. The skip is then:

> if known_hash is not None and current_hash in known:
>     … delete_by_file_paths … extra hashes …
>     return 0, "skipped", current_hash

This return is before `chunk_file`, before `embed_texts`, before `upsert_chunks`. `test_unchanged_file_is_skipped_without_embedding` fails the test if the embedder is called. The read and the hash, for their part, have already happened. If other fingerprints of the same path remain for this checkout, the skip still calls `delete_by_file_paths` on those old fingerprints (`test_matching_blob_detaches_stale_hashes`).

If the pair `(path, fingerprint)` is already in the snapshot, the return is `attach`, again before the parse. The attaches are grouped at the end (`attach_checkouts`), then the old fingerprints of the same path are detached. `test_second_branch_links_shared_blob_without_embedding` forbids the embedder call in that case.

A new fingerprint follows the other branch: `chunk_file`, one `embed_texts` on all the texts of the file, `upsert_chunks`, then the detachment of the old fingerprints. A changed byte changes the fingerprint of the entire file. All the chunks of that file go back to the embedder. The gap between this behavior and the two guides ("only ever touches that function's points", identifier formula without `git_blob_hash`) is detailed in [`indexation-gros-depot.md`](indexation-gros-depot.md#second-pass-and-another-checkout); the behavior that avoids the embedder is the `skipped` / `attach` return of `process_file`.

`compute_blob_hash` uses the `git hash-object` algorithm (`sha1("blob {len}\0" + content)`) only if `git` is in the `PATH` and `source_root` is a work tree (`_is_git_repo`, cached). Otherwise the stored field is `sha1(content)`. The `Containerfile` `containers/indexer/Containerfile` starts from `python:3.11-slim-bookworm` and does not install `git`. When `git` is absent from the `PATH`, `compute_blob_hash` stores `sha1(content)`. The repository does not say whether the base image already contains a `git` binary.

After the loop, the paths of this checkout's snapshot absent from `seen` go to `delete_by_file_paths(stale)`, without a fingerprint filter:

> stale = [path for path in existing if path not in seen]

`delete_by_file_paths` removes `self.checkout_id` from these points. A point that still has another checkout is rewritten. A point with no remaining checkout is deleted. The guide `reindexing-the-codebase.md` calls this a tombstone and says that the JSON summary carries `files_skipped` and `files_deleted`.

No test, guide or comment on this path gives a duration. The operations of the second pass on an unchanged tree can be named: one scroll of payloads, one directory walk, one read and one hash per retained file, zero `chunk_file`, zero `embed_texts`, zero `upsert`. They cannot be ranked by time.

## The functions that know a path

`build_arg_parser` in `services/indexer/main.py` has two arguments: `--source-root` and `--watch`. No positionals, no file list. The image has `ENTRYPOINT ["python", "main.py"]` and `CMD []` (`containers/indexer/Containerfile`). A Compose `command` can add `--watch`. `python scripts/deploy.py index` adds nothing: `cmd_index` runs `podman compose --profile index run --rm indexer` (`scripts/host/deploy/stack.py`).

`scripts/deploy.py` is the host entry point; the implementation is `scripts/host/deploy/`. The help of `index` says "recreate tools mount from CODEBASE_PATH, then re-index" (`scripts/host/deploy/cli.py`). The help of `workspace --update` says "incremental reindex of this tree into the shared repository collection". Both commands end up in `main()` without paths, hence in `index_source_root` on the whole `INDEXER_SOURCE_ROOT`. "Incremental" in this help is the fingerprint skip of the entire batch.

Two functions know a path.

`process_file(path, root, store, known_hash=…, shared_blobs=…)` reads the file, hashes, then skips, attaches, or parses and embeds. Without `known_hash`, the test `known_hash is not None` is false: no skip. Without `shared_blobs`, no attach. A bare call re-embeds a file that has chunks. If `read_bytes` raises `OSError`, the return is `(0, "empty", "")` and no deletion is called. A deleted path passed to `process_file` does not set a tombstone.

`apply_watch_event(src_path, kind, root, store)`:

- `deleted`: `delete_by_file_paths([rel_path])` without a fingerprint filter, then a `file_tombstoned` log. The `is_source_file` filter does not apply to `deleted`.
- other kind: immediate return if the path is not a source file. Otherwise a full `snapshot()`, then `process_file` with the hashes of that snapshot. An `attach` is written right away, and the old fingerprints of that path are detached.

`_run_watch` also sends moves: a moved file becomes a `deleted` on the old path and a `changed` on the new one. A moved directory detaches the snapshot paths that have this prefix, then submits the sources still under the destination. The debounce is 0.5 s (`WATCH_DEBOUNCE_SECONDS`). Each `changed` redoes its own scroll. The batch does only one.

A caller that has only a list of commit paths therefore has to call, for each path still on disk, `process_file` with a snapshot (or `apply_watch_event` as `changed`), and, for each vanished path, `delete_by_file_paths` or `apply_watch_event` as `deleted`. `index_source_root` does not take this list. Its variable `seen` is the whole walk. Reusing the line `stale = [path for path in existing if path not in seen]` with a `seen` limited to the commit would detach all the other paths of this checkout.

The code does not contain this caller. This note does not write it.

## A commit is not the working tree

The indexer reads the mounted directory. Compose mounts `${CODEBASE_PATH:-./sample}` on `/workspace/app` as `:ro` for the `indexer` service, with `INDEXER_SOURCE_ROOT` and `INDEXER_CHECKOUT_ID` (`compose.yaml`, repeated in `CONTRACTS.md`). `cmd_workspace` / `_run_repo_index` replaces, for this one `run`, `CODEBASE_PATH` with the checkout, `QDRANT_COLLECTION` with the remote's collection, `INDEXER_SOURCE_ROOT` with `/workspace/app`, and `INDEXER_CHECKOUT_ID` with the checkout's identifier. The process then hashes the bytes of `read_bytes`. It does not run `git show` or `git diff`.

Consequence for files already committed: they enter the index when the walk sees them and their fingerprint on disk is not already linked to this checkout. If the working tree of that path still differs from the committed blob (unstaged modifications left in the same file), the stored fingerprint is the one on disk.

Consequence for files still dirty or untracked: the walk takes them too, as soon as the extension or the name is retained. C++: `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`, `.h` (`SOURCE_EXTENSIONS`). Text: the suffixes and names of `TEXT_LANGUAGE_BY_SUFFIX` and `TEXT_LANGUAGE_BY_NAME` in `services/indexer/text_chunker.py` (Markdown, CMake, YAML, JSON, TOML, `.ini`, `.cfg`, `.txt`, `.sh`, `CMakeLists.txt`, `Containerfile`, `Dockerfile`, `.clang-tidy`, `.clang-format`). The sentence "local edits stay out of the index" is the message of `cmd_workspace` when it does not launch the indexer:

> print("[deploy] repository already indexed; local edits stay out of the index")

The executed condition is `recorded.needs_index or recorded.new_checkout or args.update`. Without `--update`, an already marked remote and an already registered checkout skip the batch. With `--update`, or with `deploy.py index`, the batch reads the disk as it is, including what is not committed.

Consequence for deletions. The walk no longer sees the path. If it was in `checkout_hashes`, it is in `stale` and `delete_by_file_paths` detaches this checkout. A point still carried by another checkout stays. `apply_watch_event(..., "deleted")` does the same detachment for a named path. `process_file` on an absent file does not.

Consequence for renames. The batch has no rename detection. The old path is missing from `seen` and is detached. The new path is a file read, hashed, then skipped, attached or embedded. The watcher makes the pair explicitly (`on_moved`). A list that contains only the new name leaves the old one in `checkout_hashes`.

`git diff-tree` is the documented way to find the paths of a commit, because `post-commit` receives none (next section). The page [git-diff-tree](https://git-scm.com/docs/git-diff-tree) says: "If there is only one `<tree-ish>` given, the commit is compared with its parents." `-r` "Recurse into sub-trees." `--name-status` "Show only the name(s) and status of each changed file." `--name-only` "Show only the name of each changed file in the post-image tree." `--no-commit-id` suppresses the identifier line that the command prints otherwise. The status letters, on the same page, are `A` addition, `C` copy, `D` deletion, `M` modification, `R` rename, `T` type change, `U` unmerged, `X` unknown, `B` broken pairing. `C` and `R` are followed by a score. The same page says that copied or renamed entries "cannot appear if detection for those types is disabled", and `-M` / `--find-renames` is the option that enables detection. `--name-only` gives only the name in the destination tree: the old name of a rename is not in it.

For a merge commit, the page says that `-m` shows the differences from each of the parents, that `-c` lists only the files modified from all parents, and that the raw format can take `-c` or `--cc` "to generate diff output also for merge commits". With `-c` or `--cc`, "only the destination or final path is shown even if the file was renamed on any side of history", except with `--combined-all-paths`. The comparison is between the commit trees, not the working tree.

A commit for which this comparison lists no indexable file leaves Qdrant as it is if the indexer is called only on that list. The entire batch, for its part, redoes the scroll, the walk, the reads and the hashes, then skips, and the `stale` sweep anyway.

Several checkouts share one collection. The guide `docs/guides/reindexing-the-codebase.md`:

> Each repository is one Qdrant collection, named from the remote URL. Two
> tickets of that repository share the collection.

Indexing checkout A writes A's `INDEXER_CHECKOUT_ID`. `delete_by_file_paths` and `attach_checkouts` modify only that identifier. Checkout B keeps its links until its own batch. A commit in A does not refresh B's `checkouts` filter. A blob already embedded by B can be attached to A without a new embedding, if A's batch sees the same pair `(path, fingerprint)`.

## What the agent adds for uncommitted changes

The guide `reindexing-the-codebase.md` says that the agent "adds `svn status` or `git status` for uncommitted files". `docs/concepts/rag-explained.md` specifies that these dirty files are placed in front of the chunks, "marked as the working copy, not written back into Qdrant".

The agent's code goes beyond that sentence, and it stays short of a diff.

`services/tools/handlers/git.py`, `run_git_status`:

> Uncommitted names. Used to overlay a checkout; not an LLM tool.

The command is `git status --porcelain=v1 -u`. The output is statuses and names. `services/agent/checkouts.py`, `parse_status_paths`, "Deleted paths are omitted". For Git, a status that contains `D` is discarded, `!!` and an empty status too, and a line `old -> new` keeps only the name after ` -> `. `test_git_status_uses_the_new_name_of_a_rename` expects `src/calculator.cpp` and `src/renamed.cpp`, not `src/old.cpp` nor `src/gone.cpp`. `is_overlay_source` keeps only the C/C++ suffixes (`.c`, `.cc`, `.cpp`, `.cxx`, `.h`, `.hh`, `.hpp`, `.hxx`, `.inl`, `.ipp`) and the name `CMakeLists.txt`. A dirty `.md` or `.yaml` does not enter this list, whereas the indexer does walk them.

`services/agent/working_copy.py` takes at most `MAX_OVERLAY_FILES` (6) of these paths and re-reads them through `/read-file`, over a line window (`MAX_OVERLAY_LINES` = 80, or the hit's window). The module says: "`svn status` and `git status` name the files. `/read-file` on `tools` reads them. The index stays the shared revision". `local_changes_note` pastes the status stdout into the prompt, truncated, with the sentence "not written into the shared index". This stdout is the porcelain: statuses and names. `run_git_diff` exists alongside and requires two refs. The overlay does not call it.

This path therefore covers: the porcelain names of the working tree, the new name of a rename, the re-read text (not the patch) of at most six C/C++ sources or `CMakeLists.txt`. It does not remove from Qdrant a file deleted in the working tree. It does not replace a reindex.

## Where to put the hook, and which existing command it can launch

[githooks](https://git-scm.com/docs/githooks):

> Before Git invokes a hook, it changes its working directory to either `$GIT_DIR` in a bare repository or the root of the working tree in a non-bare repository.

The variables `GIT_DIR`, `GIT_WORK_TREE`, etc. are exported. The same page says to unset them before Git commands in another repository. `post-commit`:

> This hook is invoked by `git-commit`. It takes no parameters, and is invoked after a commit is made.
> This hook is meant primarily for notification, and cannot affect the outcome of `git commit`.

The hook therefore sees the work tree after a successful `git commit`, without a file list. `HEAD` is the commit just made. The work tree may still contain unstaged modifications: the hook does not receive a tree isolated from the commit.

The same page attaches other commands to other hooks.

- `post-merge`: invoked by `git-merge`, "which happens when a `git pull` is done on a local repository". One parameter: squash merge or not. "not executed, if the merge failed due to conflicts."
- `post-checkout`: invoked when `git-checkout` or `git-switch` has updated the work tree. Three parameters: the old HEAD, the new HEAD, and a flag branch (1) or file (0). Also after `git clone` and `git worktree add`, except with `--no-checkout`.
- `post-rewrite`: invoked by `git commit --amend` and by `git-rebase`. First argument `amend` or `rebase`. On stdin, the rewritten commits `old SP new`. The page does not say that an amend skips `post-commit`: the amend is a `git commit`, and `post-rewrite` comes in addition. It mentions neither `git reset` nor `git cherry-pick`. It cannot be deduced from this page that they run or skip `post-commit`.

`post-rewrite` gives commit identifiers, not paths. Finding the files remains a tree comparison, of the `git diff-tree` kind.

In this repository, the only hook shipped is `scripts/git-hooks/post-commit.train.sample`. `scripts/host/install_train_hook.py` copies it to `.git/hooks/post-commit` of the `CODEBASE_PATH` repository read from `.env` (default `./app` if the key is missing; `CONTRACTS.md` and Compose have another default, `./sample`). If `.git` is a worktree file, the destination is the `gitdir` it points to. The guide `docs/guides/fine-tuning-the-codebase.md` says: "Install a post-commit hook into `CODEBASE_PATH`" via `python scripts/host/install_train_hook.py`, and to edit the sample if the WSL distro or the repository path differs. The installer does not walk `state/workspaces.json`. A checkout registered by `workspace` does not receive this file, unless it is itself `CODEBASE_PATH`.

The sample is a `#!/bin/sh` that calls `wsl -d local-llm` then:

> cd /mnt/d/rest/pumba && python3 scripts/deploy.py train --incremental --skip-index

The lock it tests is `models/training/.train.lock`, not `state/index.lock`. `--skip-index` is the argument of `train` that "do not re-run the indexer after training" (`cli.py`). This hook does not index. It lives in the `CODEBASE_PATH` checkout, with hardcoded paths to a `pumba` repository and a `local-llm` distro. The `indexer` service, for its part, then runs in Podman and reads the bind mount. The docstring of `services/indexer/main.py` says not to count on `--watch` across "Windows host -> Podman machine -> WSL2 -> bind-mount", and to relaunch the one-shot batch. A hook that launches this one-shot does not go through inotify.

What the existing commands trigger, if a hook called them.

`cmd_index`: `_require_podman`, `_require_bind_source`, `_recreate_mount_dependents`, then the indexer `run`. `_MOUNT_DEPENDENT_SERVICES` is `tools`, `patch`, `agent`, `ui-proxy` (`scripts/host/deploy/__init__.py`). The recreation is `podman compose up -d --force-recreate --no-deps` of these four services; `serena`, which also mounts `CODEBASE_PATH`, is not part of it. No `IndexLock`. The indexed tree is that of `CODEBASE_PATH` in the Compose environment, with the collection and `INDEXER_CHECKOUT_ID` of that environment (`code_chunks` and `default` in `CONTRACTS.md` if nothing replaces them). A commit in another checkout, with a hook that calls `deploy.py index`, re-indexes that other tree.

`cmd_workspace` with `--update`: registers the checkout, rewrites the mounts, recreates the four services only if the mounts changed, then `with registry.IndexLock()` and `_run_repo_index`. The lock is `state/index.lock` ("One indexer at a time. A lock older than six hours is stolen."). The `run` has `--no-deps`. The guide `reindexing-the-codebase.md` says "One indexer runs at a time" without distinguishing `index` and `workspace`. The code takes the lock only in `cmd_workspace`.

The batch launched by either command is the one the code supports: the entire tree, fingerprint skip, tombstones at the end of the walk. There is no argument to pass only the commit's paths.

## What a commit list would change

Three levels, kept separate.

### Facts from the code

The skip by `git_blob_hash` avoids the parse, the embedder and the upsert of a file whose bytes read are already linked to this checkout. It does not avoid the walk, the read, the hash, nor the snapshot scroll. The tombstone of paths absent from disk is the `checkout_hashes` / `seen` comparison at the end of `index_source_root`, or a `delete_by_file_paths` / a `deleted` event. `process_file` does not detach an unreadable file. Detachment and attachment concern only the process's `INDEXER_CHECKOUT_ID`. The agent does not rewrite Qdrant: it names dirty files and re-reads a few of them. The only hook of the repository runs `train --skip-index`. The `index` and `workspace --update` commands run the entire batch.

### Consequence

A list drawn from `git diff-tree` (a commit against its parents) avoids, for the files outside the list, the walk, the read and the hash. The parse and the embedder for these files are already absent from the second pass when the fingerprint matches. The snapshot scroll remains necessary if the skip and the attach are wanted: without `known_hash`, `process_file` re-embeds. `apply_watch_event` as `changed` redoes this scroll for every path; the batch does only one.

The same list misses what the entire batch picks up by the difference between the snapshot and the disk. A deleted path or the old name of a rename, if it is not detached separately, stays linked to this checkout. `--name-only` provides only the destination name. The agent's parser discards the `D` entries and keeps only the new name. This list contains neither the deletions nor the old path. Applying the batch's `stale` computation to a `seen` equal to the commit alone would detach the rest of the checkout.

The bytes hashed are those of the working tree at the time of the `run`. A file of the commit still modified on disk enters Qdrant in that dirty form. The other checkouts of the collection do not move. A commit without an indexable file changes no point if one sticks to the list; the entire batch still redoes scroll, walk and hash. The first fill of a tree absent from the snapshot remains a complete walk: a commit's list does not contain the files that commit did not touch.

`deploy.py index` from a `post-commit` is a complete batch of `CODEBASE_PATH`, plus the recreation of the four services, without the lock. `workspace --update` on the checkout that was just committed is a complete batch of that tree, with the lock, and without recreation as long as the mounts have not changed. Both read the disk. Neither reads the commit's list.

`post-commit` alone is the hook that the `githooks` page attaches to `git commit`. A successful merge, a checkout or a switch, an amend and a rebase are described on `post-merge`, `post-checkout` and `post-rewrite`. An index hooked only on `post-commit` follows the commits of `git commit`. The page does not make it follow a merge, a branch change or a rebase.

### What the repository does not decide

The repository describes the one-shot on the tree as the supported path, and the fingerprint skip as what makes a second pass without the embedder. It does not say that a list of commit paths is preferable, nor that it should be rejected. It does not provide the command that would take only those paths. The choice of writing it, and of covering or not merge, rebase and checkout through the hooks that Git names for those commands, is not settled in the code, the guides or `CONTRACTS.md`.
