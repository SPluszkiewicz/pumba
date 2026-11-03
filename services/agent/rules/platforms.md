---
globs:
  - "**/*"
---
Windows and Unix:

- Source, CMake, and scripts in the tree use LF. A Windows-only `.bat` or `.ps1` may use CRLF; do not convert the rest of the tree.
- Paths in committed files use forward slashes. No drive letters, no `/mnt/<letter>`, no backslash escapes in C++ or Python source.
- C++ uses `std::filesystem`. Python uses `pathlib`. Do not call Win32 or POSIX APIs unless the file is already inside a platform-specific source.
- Scripts that the containers run are LF and have a shebang. Do not assume `python` versus `python3` or `sh` versus PowerShell without saying which host.
