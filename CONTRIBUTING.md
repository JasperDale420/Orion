# Contributing

## Getting Started

1. Create a branch from `master`.
2. Install dependencies: `uv sync`.
3. Configure local env from `.env.example`.
4. Run tests before opening a PR.

## Branch Strategy

- Use prefixed branches for codex-driven work: `codex/<short-topic>`.
- Keep PRs focused and small when possible.

## Pull Request Process

1. Update docs/changelog with your change.
2. Run quality gates locally.
3. Push branch and open PR.
4. Resolve review comments and re-run checks.

## Code Style

- Ruff + Black + MyPy are required gates.
- Keep logic in vertical slices.
- Add structured logging around failure boundaries.

## Testing Requirements

Minimum before merge:

```bash
uv run pytest -q
ruff check .
mypy .
```

## Commit Messages

- Use clear, scoped messages (`docs: ...`, `fix: ...`, `feat: ...`).
- Include `@codex review` in commit messages for codex-driven changes.
