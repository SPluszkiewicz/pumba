---
- "**/*"
---

# Test-Driven Development

TDD is the red to green loop. This skill is the reference that makes that loop produce tests worth keeping: what a good test is, where tests go, the anti-patterns, and the rules of the loop. Every section applies on every cycle: consult them before and during the loop, not after.

Tests verify behavior through public interfaces, not implementation details. A good test reads like a specification and survives a refactor.

A seam is the public boundary you test at. Test only at pre-agreed seams. One seam, one test, then only enough code to pass it.

Anti-patterns: do not mock your own code, do not test private methods, and do not recompute the expected value the way the implementation does. Expected values are known literals.

Rules of the loop:
- Red before green. Write the failing test first.
- One slice at a time. Do not write the next test until this one is green.
- Refactoring is not part of this loop.

Source: Matt Pocock, skill `tdd`, MIT, https://github.com/mattpocock/skills
