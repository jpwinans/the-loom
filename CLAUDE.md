# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

**The Loom** is a knowledge-graph substrate with a single JSON-in/JSON-out CLI,
built on **[FalkorDB](https://www.falkordb.com/)**. It exposes **164 commands**
across 23 categories plus a special `init` command and a set of high-level
composites. The command catalog is **[COMMANDS.md](COMMANDS.md)** (generated from
the registry — never hand-edit it) and the user-facing overview is
**[README.md](README.md)**.

## Architecture invariants

These are load-bearing. Preserve them.

1. **One transactional store.** Graph, entity vectors, document chunks, and
   full-text all live in FalkorDB. Do not add a separate file store, a separate
   vector store, or file-based locks. Every mutation is a single atomic query
   plus an append to the event log.
2. **Event-sourced and bi-temporal.** Mutations are append-only events; current
   state is a projection. Updates **invalidate; they never overwrite in place.**
   History is real and queryable — *state as of time T* is a first-class
   operation (`tx_from` / `tx_to` system-time bounds on every record).
3. **The domain model is the single source of truth.** `theloom/model.py`
   (Pydantic v2) defines every entity/relation shape and validates on load.
   Python attributes are snake_case; wire names (JSON aliases) are camelCase.
4. **Typed error codes.** Errors carry one of `PARSE_ERROR`, `INPUT_REQUIRED`,
   `VALIDATION_ERROR`, `NOT_FOUND`, `OPERATION_ERROR`, `CONFIG_ERROR`; the JSON
   error goes to stderr and the process exits non-zero. Never classify errors by
   substring-matching prose.
5. **One config path.** Configuration resolves through a single code path
   (`theloom/config.py`) — no divergent server/CLI behavior.
6. **Prefer libraries over home-grown logic.** Graph algorithms, semiring
   algebra, embeddings, document parsing, and symbolic math are delegated to
   maintained libraries (see [STACK.md](STACK.md)); don't reinvent them.

## Toolchain

```bash
uv sync                          # install dependencies into the venv
docker compose up -d falkordb    # start the store
uv run loom --help               # run the CLI
uv run loom init                 # initialize the default graph
uv run pytest                    # tests
uv run ruff check . && uv run ruff format .
uv run mypy --strict theloom
uv run loom --generate-docs > COMMANDS.md   # regenerate the command catalog
```

Keep `main` green: typecheck + lint + tests pass on every push. No wall-clock
performance assertions gate CI (track performance as reported benchmarks).

## Layout

```
theloom/                     the package
  model.py                     Pydantic domain model (single source of truth)
  config.py                    configuration (single resolution path)
  errors.py                    typed error codes
  cli/                         Typer CLI (io, app, command registry, docs)
  store/                       FalkorDB store, read port + in-memory adapter, event log,
                               lifecycle, filters, migration
  graph/  semantic/  algebra/  synthesis/
  documents/  extraction/  verification/  operations/  composites/
  viz/                          TapestryBundle assembly + HTML template injection
tests/                       test suite
  fixtures/                    shared test fixtures
tapestry/                    frontend workspace (Vite/React/sigma.js SPA, contributor-only)
docker-compose.yml           FalkorDB service
pyproject.toml               project + tooling config (ruff, mypy, pytest)
```

## Conventions

- **Behavior first.** When changing a command, keep its input schema, output
  shape, and error codes stable unless the change is the point; add a test that
  pins the new behavior.
- **Snake/camel boundary.** Internal Python is snake_case; anything crossing the
  CLI/JSON boundary uses the model's camelCase aliases. Let the model do the
  translation — don't hand-serialize.
- **The registry is the source of the CLI.** Commands are declared in
  `theloom/cli/registry.py`; `COMMANDS.md` is regenerated from it.
