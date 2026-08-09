# Full-use test: the orchestrator's session, rerun on the new substrate

This is a reviewer-runnable acceptance walkthrough for every desire this PR
implements, written from the perspective of the use The Loom was being put to
*before* the PR: an orchestrating agent running a multi-agent verification
campaign, keeping its loop state in a ledger graph, checking sub-agents' claims
against the store, policing scratch-graph hygiene by prompt, and carrying its
memory as markdown files. Each pain of that workflow became a numbered desire;
each stage below exercises the corresponding feature and states the assertion
that must hold. Run the stages in order — later stages build on earlier state.

**Prerequisites:** `uv sync` · `docker compose up -d falkordb` · shapes below
verified at `12a9b66`; every command also answers `--schema`.

Conventions: `$SESSION`, `$NS`, entity ids, world ids, and event ids are values
you capture from earlier outputs. `jq` is convenient but optional.

---

## Stage 0 — Open a workspace instead of policing a prefix *(desire 2)*

Before: the orchestrator enforced "only touch graphs prefixed `tl477-`" across
ten agents by prompt, and cleanup still needed `redis-cli`.

```bash
uv run loom begin-session '{"name": "pr-test", "ttlSeconds": 3600}'
```

**Assert:** the response carries `sessionId`, a `namespace` (call it `$NS`,
e.g. `sess-ab12…-`), `expiresAt`, `applied: true`, and `eventIds` — the first
write-receipt of the session (desire 1 is already at work).

```bash
uv run loom create-entity '{"graph": "'"$NS"'ledger", "name": "probe", "entityType": "concept", "observations": ["ad-hoc graph, never explicitly created"]}'
uv run loom list-graphs '{}'
uv run loom list-sessions '{}'
```

**Assert:** `$NS`ledger — created implicitly by a bare `graph:` param — appears
in `list-graphs` (the registry gap this PR closes) and inside the session's
`graphs` list. No `redis-cli` anywhere in this walkthrough.

---

## Stage 1 — Seed the ledger; every write hands you a receipt *(desires 1, 9, 11, 14 groundwork)*

Before: a builder said "done", and five blind critics re-ran commands to check
what the store actually held.

Create a small verification-campaign ledger — two claims under test, evidence,
and authorship (`session`) on every assertion:

```bash
G="$NS"ledger
uv run loom create-entity '{"graph": "'"$G"'", "name": "envelope-invariant-holds", "entityType": "claim", "observations": ["No command returns a bare top-level array"], "confidence": {"score": 0.6, "basis": "inference"}, "session": "orchestrator"}'
uv run loom create-entity '{"graph": "'"$G"'", "name": "fixture-race-fixed", "entityType": "claim", "observations": ["The resultset-cap fixture cannot strand the server"], "confidence": {"score": 0.7, "basis": "inference"}, "session": "orchestrator"}'
uv run loom create-entity '{"graph": "'"$G"'", "name": "critic-transcript-1", "entityType": "evidence", "observations": ["Registry walk over all commands found zero bare arrays"], "confidence": {"score": 0.9, "basis": "direct_observation"}, "session": "critic-a"}'
uv run loom create-relation '{"graph": "'"$G"'", "from": "<critic-transcript-1 id>", "to": "<envelope-invariant-holds id>", "relationType": "supports", "polarity": null, "strength": "strong", "evidence": "blind sweep"}'
```

**Assert (desire 1):** every mutating response above carries `eventIds`.
**Assert (desire 11):** the `create-relation` response carries `fromName` /
`toName` beside the ids — no join needed to read it. So do `read-relation`,
`list-relations`, and `merge-entities` if you try them.
**Assert (desire 9):** every list you run in this walkthrough returns
`{"items": [...], "count": N, "notices"?}` — `count == len(items)`, no bare
arrays, no per-command dialects:

```bash
uv run loom list-entities '{"graph": "'"$G"'"}'
uv run loom list-relations '{"graph": "'"$G"'"}'
```

---

## Stage 2 — Verify a sub-agent mechanically, not socially *(desire 1)*

Before: "did the sub-agent actually do what it reported?" cost a dispatched
critic. Now it is one read. Take any `eventIds` value from Stage 1 (the
relation's, say `$EV`):

```bash
uv run loom what-changed '{"graph": "'"$G"'", "eventIds": ["'"$EV"'"]}'
```

**Assert:** the replay returns `{entity, field, old, new, causedBy}` rows in
the uniform envelope, `causedBy` naming the real command (`create-relation`),
old→new values exact, and names resolved on every row. Update an entity's
confidence, replay its receipt, and confirm the `old` is the value you
started from — the response and the store's truth are no longer two things to
reconcile.

---

## Stage 3 — Program against honesty ahead of time *(desires 3, 7)*

Before: `NOT_PERSISTED` / `DRY_RUN` handling was discovered per-command,
per-run.

```bash
uv run loom notices-catalog '{}'
```

**Assert:** every notice code ships with a meaning and the commands that emit
it — including codes born in this PR (`ALREADY_REAPED`, `CONTESTED_ON_MERGE`,
`WORLD_PROJECTION_PARTIAL`, `INSUFFICIENT_DATA`, `CONFIDENCE_OUT_OF_LINE`,
`ALL_DREAMS_REVIEWED`) — plus an `alerts` section for the session-alert
vocabulary. Trigger two live to taste them:

```bash
uv run loom propagate-credit '{"graph": "'"$G"'", "entityIds": ["<critic-transcript-1 id>"], "delta": 0.1, "dryRun": true}'   # → DRY_RUN, applied: false
uv run loom list-entities '{"graph": "'"$G"'", "limit": 1}'                                                                   # → TRUNCATED
```

**Assert (desire 7, the enforcement):** the contract is *tested*, not prose —
run the walks and try to drift them:

```bash
uv run pytest tests/test_notices_catalog.py tests/test_documented_defaults.py -q
```

Add a phantom entry to `NOTICE_CATALOG` or flip a documented default's code
path, rerun, and watch the exact test fail. Revert.

---

## Stage 4 — Know the embedder's geometry before trusting it *(desires 8, 10)*

Before: a builder had to discover live that "unrelated" pairs score ~0.48 on
this embedder, not ~0, before any threshold was defensible.

```bash
uv run loom embedder-profile '{}'
```

**Assert:** measured `unrelatedPairBaseline` (mean ≈ 0.47–0.49 on
`nomic-embed-text-v1.5`), `relatedPairRange`, a `meaningfullyRelatedCutoff`,
and a `cutoffMethod` string that discloses *how* — including that the bands
overlap. Edit a probe pair in `theloom/semantic/landscape.py`, rerun, and the
numbers move: it is a measurement, not a constant. Revert.

Now grounding that respects meaning (desire 10) — embed, then verify a
paraphrase and a false friend:

```bash
uv run loom embed-entities '{"graph": "'"$G"'"}'
uv run loom verify-fidelity '{"graph": "'"$G"'", "text": "The registry sweep confirmed that no command hands back an unwrapped list at the top level."}'
uv run loom verify-fidelity '{"graph": "'"$G"'", "text": "The postal envelope was stamped and sealed before mailing."}'
```

**Assert:** the first grounds `envelope-invariant-holds` with `matchBasis:
"semantic"` and a disclosed `matchScore`/`zScore`/`zCutoff`; the second — one
shared word, wrong sense — is `omitted`, **with the same audit fields
populated** so you can reconstruct *why* from the response alone. An honest
no is as auditable as a yes.

---

## Stage 5 — "What would change if I stop believing this?" *(desire 4)*

Before: `propagate-credit` computed exactly this trajectory but coupled it to
a write or a dry-run's flat preview.

```bash
uv run loom belief-blast-radius '{"graph": "'"$G"'", "entityIds": ["<critic-transcript-1 id>"], "delta": -0.4}'
```

**Assert:** `applied: false`; a `propagation` list showing which claims'
confidences are load-bearing on that evidence (old → new per hop); a `diff`
derived from real events; and afterwards `list-worlds '{"includeReaped":
true}'` shows **no** leftover world — the composite forked, propagated,
diffed, and purged. Re-read the claim in `main`: untouched. One propagation
engine, zero permanent residue.

---

## Stage 6 — Counterfactuals as substrate, not simulation *(desire 12)*

Before: counterfactuals lived lossily inside one context window and
evaporated by the next sentence.

```bash
uv run loom fork-world '{"graph": "'"$G"'", "name": "what-if-critic-wrong", "ttlSeconds": 3600}'      # → $W, O(1), writes no entity data
uv run loom update-entity '{"graph": "'"$G"'", "world": "'"$W"'", "id": "<envelope claim id>", "confidence": {"score": 0.2, "basis": "inference"}}'
uv run loom read-entity  '{"graph": "'"$G"'", "id": "<envelope claim id>"}'                            # main: still 0.6
uv run loom read-entity  '{"graph": "'"$G"'", "world": "'"$W"'", "id": "<envelope claim id>"}'         # fork: 0.2
uv run loom diff-worlds  '{"a": "main", "b": "'"$W"'"}'
```

**Assert:** the diff lists exactly the fork's writes — old → new confidence
with the causing `eventId` (desire 1 doing double duty), names beside ids
(desire 11), uniform envelope (desire 9). Now manufacture a conflict: revise
the *same* claim in `main` too, then:

```bash
uv run loom merge-world '{"from": "'"$W"'", "into": "main", "strategy": "endorse-all"}'
```

**Assert:** the contested claim comes back under a `CONTESTED_ON_MERGE`
notice, only the uncontested set applies, `applied` tells the truth. Retry
with `{"strategy": "select", "entityIds": [...]}` to graft explicitly. Then
`abandon-world` and verify: `main`'s event log is byte-identical to before
the fork existed (capture `what-changed` output before/after and compare) —
worlds are invisible to non-world consumers.

---

## Stage 7 — Insight generation while no one is invoking *(desire 13)*

Before: `detect-loops`, `contested-claims`, `stale-beliefs`,
`hypothesis-engine` existed as commands someone had to remember to run.

Plant what a night should find: a contradiction that is only *transitive*
(create `rival —contradicts→ bridge —supports→ envelope-invariant-holds`,
plus an inference rule `A contradicts B ∧ B supports C ⇒ A contradicts C`
via `inference-rule-create`), and a structural gap (one zero-relation entity).

```bash
uv run loom consolidate '{"graph": "'"$G"'"}'
uv run loom consolidate '{"graph": "'"$G"'"}'          # a second night, no review between
uv run loom since-last-session '{"graph": "'"$G"'"}'
```

**Assert:** each run forks an independent `dream-<date>` world (different
`worldId`s, findings not compounding); the dream contains a `tension` marked
transitive and a `hypothesis` for the gap, at low confidence
(`inference`/`speculation`, never higher), provenance
`consolidation/<pass>`; `main`'s event log is untouched; the report hands you
the exact `diff-worlds` handle. `since-last-session` returns it all in one
hard-capped envelope (`truncated` marker if trimmed) with `calibrationAlerts`
included. Morning review is a merge decision: `merge-world select` exactly
one finding into `main`, `abandon-world` the rest, and re-run
`since-last-session` — it now says `ALL_DREAMS_REVIEWED`, not a false "never
consolidated".

---

## Stage 8 — Confidence that earns its numbers *(desire 14)*

Before: nothing ever told the orchestrator whether its 0.8s resolve true 80%
of the time.

Resolve the campaign's claims (both were verified during this PR's own run):

```bash
uv run loom resolve-claim '{"graph": "'"$G"'", "claimId": "<envelope claim id>", "resolution": "confirmed", "evidence": "registry-walking test + live sweep"}'
uv run loom resolve-claim '{"graph": "'"$G"'", "claimId": "<fixture claim id>", "resolution": "confirmed", "evidence": "red/green concurrency proof"}'
uv run loom calibration-profile '{"graph": "'"$G"'", "minBucketN": 1}'
```

**Assert:** the `orchestrator` bucket reports count, mean **asserted**
confidence, empirical hit rate, Brier score, and gap — hand-check the
arithmetic (two confirmations at 0.6 and 0.7 ⇒ mean 0.65, hit rate 1.0,
Brier = ((0.6−1)² + (0.7−1)²)/2 = 0.125, gap −0.35). Now revise a resolved
claim's confidence to 0.99 and re-run the profile: **the numbers do not
move** — assertion-time is read from history, immune to hindsight, including
for claims that entered `main` via a world merge. Buckets below the floor
report `INSUFFICIENT_DATA`, never a fabricated number. Finally, assert far
above your measured hit rate in a bucket with enough history and watch
`CONFIDENCE_OUT_OF_LINE` arrive on the create response — a nudge, never a
rejection — and try `propagate-credit` with `"dampingFactor": "calibrated"`
to see per-hop damping resolved from the author's measured reliability
(`dampingApplied` disclosed per hop).

---

## Stage 9 — Memory as a graph, not a directory *(desire 5)*

Before: recall was filename-and-hook scanning over markdown files.

This PR's run deployed it live: graph `claude-memory` holds the agent's
memory files as embedded entities. Reproduce the pattern in miniature inside
your session (or query the deployed graph directly):

```bash
uv run loom hybrid-search '{"graph": "claude-memory", "query": "tests failing because two agents run pytest at the same time", "limit": 2}'
```

**Assert:** the top hit is the multi-agent test-hazards memory, by meaning,
not filename. Note the honesty at the miss end too: a query with no true
match scores *below* the desire-8 baseline — the substrate says "nothing
matches well" instead of pretending.

---

## Stage 10 — Tear down with one call; the substrate assumes N callers *(desires 2, 6)*

```bash
uv run loom end-session '{"sessionId": "'"$SESSION"'"}'
uv run loom end-session '{"sessionId": "'"$SESSION"'"}'    # again
docker exec theloom-falkordb redis-cli GRAPH.CONFIG GET RESULTSET_SIZE
```

**Assert:** the first call reaps every graph the session created (verify with
`list-graphs`); the second is an honest no-op — `applied: false` with
`ALREADY_REAPED`, no phantom `eventIds`. `RESULTSET_SIZE` reads 10000 — and
the desire-6 guarantee is pinned by a test that *deliberately races* the
fixture:

```bash
uv run pytest tests/test_resultset_cap_concurrency.py -q
```

---

## Epilogue — the composition (§15)

The stages compose along one spine, which is the point of the PR: the
receipts of Stage 1 are the diff rows of Stage 6 and the audit trail of
Stage 2; the worlds of Stage 6 are the safety property of Stage 7's dreams
and the engine inside Stage 5's one-liner; Stage 7's morning review feeds
Stage 8's resolutions; Stage 8's calibration flows back into Stage 7's waking
surface as alerts and into Stage 1's next assertion as a nudge. Nothing
introduced a second source of truth — every feature is a new read surface or
a new ref over the same append-only event log the orchestrator was already
trusting before this PR. The difference is that trusting it no longer
requires dispatching a critic.
