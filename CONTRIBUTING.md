# Contributing to The Loom

Thanks for your interest in improving The Loom. This document covers how to set
up a working environment, the quality bar every change must meet, and the
architectural invariants that are not up for renegotiation in a routine PR.

## Getting set up

You need **Python ≥ 3.11**, **[uv](https://docs.astral.sh/uv/)**, and
**Docker** (for FalkorDB). Then:

```bash
uv sync                          # install dependencies into the venv
docker compose up -d falkordb    # start the store (tests require it)
uv run loom init                 # initialize the default graph
uv run pytest                    # verify your environment
```

The frontend workspace (`tapestry/`) is contributor-only and needs Node 22:

```bash
cd tapestry
npm ci
npm test
```

## The quality gate

CI runs three jobs — `ci` (lint, typecheck, tests), `tapestry` (frontend
tests, build, template drift check, e2e), and `tapestry-live` (e2e against a
live server). `main` stays green: every PR must pass all of them.

Run the full gate locally before pushing:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict theloom
uv run pytest
```

Two gate details that regularly surprise people:

- **`ruff format --check` is part of CI**, not just `ruff check`. Run
  `uv run ruff format .` before committing.
- **If you touch anything under `tapestry/src`, rebuild the committed
  template in the same commit** (`cd tapestry && npm run build` regenerates
  `theloom/viz/static/tapestry.html`). CI diffs the committed template
  against a fresh build and fails on drift.

Performance is tracked as reported benchmarks, never as wall-clock assertions
in CI — don't add timing-based tests.

## Architecture invariants

These are load-bearing. A PR that violates one needs to make the case
explicitly in its description, not slip it in.

1. **One transactional store.** Graph, entity vectors, document chunks, and
   full-text all live in FalkorDB. No separate file store, no separate vector
   store, no file-based locks. Every mutation is a single atomic query plus
   an event-log append (they commit together).
2. **Event-sourced and bi-temporal.** Mutations append events; current state
   is a projection. Updates **invalidate, they never overwrite in place** —
   supersede an entity rather than deleting it, and treat *state as of time
   T* as a first-class query.
3. **`theloom/model.py` is the single source of truth** for every entity and
   relation shape (Pydantic v2). Python attributes are snake_case; wire names
   are camelCase via the model's aliases. Let the model translate — never
   hand-serialize across the boundary.
4. **Typed error codes only.** Errors carry one of `PARSE_ERROR`,
   `INPUT_REQUIRED`, `VALIDATION_ERROR`, `NOT_FOUND`, `OPERATION_ERROR`,
   `CONFIG_ERROR`. Never classify an error by substring-matching its message.
5. **One config path.** Configuration resolves through `theloom/config.py`
   alone — no divergent server/CLI behavior.
6. **Prefer libraries over home-grown logic.** Graph algorithms, semiring
   algebra, embeddings, document parsing, and symbolic math are delegated to
   maintained libraries (see [STACK.md](STACK.md)).

## Conventions

- **Behavior first.** When changing a command, keep its input schema, output
  shape, and error codes stable unless the change is the point — and when it
  is the point, add a test that pins the new behavior.
- **The registry is the source of the CLI.** Commands are declared in
  `theloom/cli/registry.py`. [COMMANDS.md](COMMANDS.md) is generated from it
  — regenerate with `uv run loom --generate-docs > COMMANDS.md`, never
  hand-edit it.
- **Tests accompany changes.** Bug fixes come with a test that fails without
  the fix; features come with tests that pin their contract. The store tests
  use a per-test `namespace` fixture so suites can run against a shared
  FalkorDB without colliding — follow that pattern.
- **Docstrings state contracts**, not narration. A comment should say what
  the code can't (a constraint, a guarantee, a why), not restate the line
  below it.

## Making a change

1. Branch from `main`.
2. Write the failing test, then the change, then run the full gate.
3. Keep commits scoped, with imperative one-line subjects ("Add X", "Fix Y")
   and a body that explains *why* when it isn't obvious.
4. Open a PR against `main` describing what changed and how it was
   validated. CI must be green to merge.

For anything that touches an architecture invariant, a command's public
contract, or the store's write path, open an issue first and propose the
design — those reviews go better before the code exists.

## Layout orientation

```
theloom/         the package (model, config, errors, cli, store, graph,
                 semantic, algebra, synthesis, documents, extraction,
                 verification, operations, composites, viz)
tests/           the test suite (fixtures in tests/fixtures/)
tapestry/        frontend workspace (Vite/React/sigma.js SPA)
docs/            architecture map, images, design docs
```

A generated architecture map lives at
[docs/architecture/ARCHITECTURE-MAP.md](docs/architecture/ARCHITECTURE-MAP.md),
with a query cheat-sheet at
[docs/architecture/QUERYING.md](docs/architecture/QUERYING.md) — both are the
fastest way to orient before a nontrivial change.
