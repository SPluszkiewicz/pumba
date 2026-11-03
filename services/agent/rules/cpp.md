---
globs:
  - "**/*.cpp"
  - "**/*.hpp"
  - "**/*.h"
  - "**/*.cc"
  - "**/*.cxx"
---
C++ (C++ Core Guidelines, C++17 minimum):

- Every resource has one owner. RAII. No raw `new`/`delete`. Transfer is `std::unique_ptr`; a borrow is a reference, `std::span`, or `std::string_view`.
- `const` by default. `enum class`. `nullptr`. `override` on virtuals. No `using` directive in a header.
- Call out undefined behavior: signed overflow, use-after-free, out-of-bounds, uninitialized reads, data races.
- Prefer standard algorithms when they stay clear. Check the claim with `lsp_definition` / `lsp_hover` for overloads, and with `compile_file` or `lint_clang_tidy` before asserting it builds or is tidy.
