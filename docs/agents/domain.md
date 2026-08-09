# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This repo is **single-context**: one `CONTEXT.md` and one `docs/adr/` at the root.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

> Both now exist: `CONTEXT.md` at the repo root and `docs/adr/` (first entry:
> `0001-soft-chunk-pointers.md`). Other prose that also carries domain weight:
> `CLAUDE.md` (architecture invariants), `STACK.md` (library choices and their
> rationale), `README.md`, and `COMMANDS.md` (generated — never hand-edit).

## File structure

Single-context repo (most repos):

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-one-transactional-store.md
│   └── 0002-event-sourced-bitemporal-model.md
└── theloom/
```

Multi-context repos put a `CONTEXT-MAP.md` at the root pointing at one `CONTEXT.md` per
context, with context-scoped `docs/adr/` directories alongside each. This repo is not one:
`tapestry/` is a single contributor-only frontend workspace, not an independent context.
If that changes, switch to a `CONTEXT-MAP.md` layout and update this file.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_

The six **architecture invariants** in `CLAUDE.md` function as standing ADRs until they are
written up as such. Contradicting one gets the same treatment: surface it, don't silently
override it.
