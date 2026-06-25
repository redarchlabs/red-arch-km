# Contributing to Red Arch Knowledge Manager

Thanks for your interest in contributing. This document describes how to propose
changes, the coding standards we follow, and the testing requirements your
contribution must meet.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting Started

1. Fork the repository and clone your fork.
2. Follow [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) to set up your local
   environment (`uv sync --all-packages`, `make dev-infra`, `make migrate`).
3. Create a feature branch from `main`:

   ```bash
   git checkout -b <type>-<short-description>
   ```

## Branch & Commit Conventions

- Branch names use a short, descriptive, kebab-case slug (optionally prefixed
  with a tracker issue id, e.g. `redarch-10-phase-8-docs`).
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):

  ```
  <type>: <description>

  <optional body>
  ```

  Allowed types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

## Code Style

### Python

- Formatted and linted with [ruff](https://docs.astral.sh/ruff/) (config in
  `pyproject.toml`: line length 120, target Python 3.12).
- Type-checked with `mypy` (strict mode).
- Run before pushing:

  ```bash
  make lint        # ruff check
  make format      # ruff check --fix && ruff format
  make type-check  # mypy
  ```

### TypeScript / JavaScript (UI)

- Formatted with Prettier and linted with ESLint:

  ```bash
  cd ui && npm run lint && npm run format
  ```

### General Principles

- Prefer many small, focused files over large ones.
- Validate input at system boundaries; never trust external data.
- Handle errors explicitly; never silently swallow them.
- Do not commit secrets. Use environment variables (see `.env.example`).

## Testing Requirements

All contributions must include tests and maintain the **80% coverage** threshold.

- **Unit tests** — individual functions, services, components.
- **Integration tests** — API endpoints and database operations (require infra).
- **E2E tests** — critical user flows (Playwright, under `ui/tests/e2e/`).

```bash
make test            # all tests
make test-unit       # unit only
make test-integration  # integration (needs `make dev-infra`)
make test-cov        # coverage report (fails under 80%)
```

Multi-tenant changes must include RLS isolation tests. Changes touching auth,
permissions, or data isolation require explicit security test coverage.

## Pull Request Process

1. Ensure `make lint`, `make type-check`, and `make test-cov` all pass locally.
2. Update relevant documentation (`docs/`, `README.md`) and `CHANGELOG.md`.
3. Push your branch and open a PR against `main`:

   ```bash
   git push -u origin <branch>
   gh pr create --base main
   ```

4. Fill in the PR description: what changed, why, the blast radius (frontend /
   backend / db / infra / docs), and a test plan. Reference the tracker issue id.
5. Address review feedback. CI must be green (lint, type-check, tests, coverage)
   before merge.
6. A maintainer merges approved PRs. Do not merge your own PR.

## Reporting Issues

Open an issue describing the problem, expected vs. actual behavior, and steps to
reproduce. For security vulnerabilities, please email **security@redarchlabs.com**
rather than filing a public issue.
