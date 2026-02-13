# DebtCutter Journal

## 2026-02-12 - Worktree Lint/Test Bootstrap Coupling
**Debt:** Tooling depended on parent-folder config (`../ruff-base.toml`) and test startup depended on host-level Numba cache defaults.
**Why it matters:** The same code passed in one checkout layout and failed in another, blocking commits and fast feedback loops.
**Next time:** Keep shared lint config inside each repo or use resolvable package tooling, and set deterministic test env vars before optional imports.
