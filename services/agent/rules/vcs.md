---
globs:
  - "**/*"
---
Version control. The mount is read-only. A change is a ```diff block the user applies on the host.

- If `.git` is at the project root, use `git_log`, `git_blame`, `git_diff`, and `git_show`. If `.svn` is there, use `svn_status`, `svn_diff`, and `svn_log`. `svn_diff` with no revisions is the uncommitted changelist. If a call says `.git` or `.svn` is missing, do not call that family again.
- Do not recommend `commit`, `push`, `update`, or `revert`. Describe the command and leave it to the user.
- One concern per change. Do not mix generated files, local config, or secrets into the diff.
- Git history is append-only on shared branches: no rewritten published history. SVN layout stays `trunk` / `branches` / `tags` when the copy already uses it.
