---
globs:
  - "**/CMakeLists.txt"
  - "**/*.cmake"
  - "**/CMakePresets.json"
---
CMake (modern, target-based):

- `target_link_libraries`, `target_include_directories`, `target_compile_features`. No directory-scope `include_directories`, `link_libraries`, or `add_definitions`.
- `PRIVATE` unless the dependency is part of the public headers. `PUBLIC` only then.
- Presets live in `CMakePresets.json`. Do not invent ad-hoc `-D` flags in docs.
- C++ standard is set explicitly (`CMAKE_CXX_STANDARD` 17 or newer, `CMAKE_CXX_STANDARD_REQUIRED ON`) before `find_package`.
- Warnings stay on (`-Wall -Wextra` or stricter).

Qt 6 projects, from The Qt Company skill `qt-cmake-project` (`references/common-mistakes.md`, BSD-3-Clause). Apply when the list uses Qt:

- `qt_add_executable` / `qt_add_library` / `qt_add_qml_module` / `qt_add_resources` / `qt_add_plugin` / `qt_add_translations`. Never `qt5_*`, never qmake (`.pro`, `QT +=`).
- After `find_package(Qt6 6.x REQUIRED ...)`, call `qt_standard_project_setup()`. Pin the same minimum version on both. It does not set `CMAKE_AUTORCC` or the C++ standard.
- QML goes through `qt_add_qml_module` (`QML_FILES`, `URI`). Not a raw `.qrc` and not `qt_add_resources` for `.qml` files.
- Do not list generated `moc_*.cpp`, `ui_*.h`, or `qrc_*.cpp`.
- Leave `RESOURCE_PREFIX` at `/qt/qml` when `QTP0001` is NEW. Do not invent arguments (`SOURCE_FILES`, `QML_SOURCES`, `MODULE_NAME`).
