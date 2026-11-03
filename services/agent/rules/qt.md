---
globs:
  - "**/*.cpp"
  - "**/*.cc"
  - "**/*.cxx"
  - "**/*.h"
  - "**/*.hpp"
  - "**/*.qml"
  - "**/*.ui"
---
Apply only when the file uses Qt (a Qt include, `Q_OBJECT`, or QML). Skip this for plain C++.

Qt 6 C++ review, from The Qt Company `agent-skills` skill `qt-cpp-review` (`platforms/windsurf.md`, BSD-3-Clause; copyright 2026 The Qt Company Ltd.):

- Models: `begin*`/`end*` for structural changes, not `layoutChanged`. `dataChanged` names the roles. `setData` emits `dataChanged` before returning true. `roleNames()` matches `data()`.
- Lifecycle: `deleteLater()` on `QNetworkReply` in the finished handler. A `QObject` created with `new` has a parent. No side effects in `Q_ASSERT`. `Q_ASSERT(ptr)` is not the only null check.
- Threads: do not write `QObject` members from `QtConcurrent::run` without synchronization. No `DirectConnection` from a worker to the GUI thread. Do not mutate a model off the GUI thread.
- Errors: check `QFile::open`, `QJsonDocument::fromJson` via `isNull()`, and `QNetworkReply::error()` before `readAll()`. Use `https`. Set `setTransferTimeout()`. Handle `sslErrors`.
- `noexcept` and `Q_ASSERT` on a precondition do not mix. `Q_ASSERT` on an invariant is fine.
- Performance: no `QRegularExpression` in a loop. `const auto&` when ranging over a shared container. `.value()` for a read of a shared `QHash`/`QMap`, not `operator[]`.
- API: `Q_PROPERTY` is `FINAL` unless a subclass must override it. Call `(std::min)(a, b)` / `(std::max)(a, b)`. Timeouts are `QDeadlineTimer` or `std::chrono`, not a bare `int`.
- QML type files are UpperCamelCase. Do not invent a `.pro` file next to `CMakeLists.txt`.
