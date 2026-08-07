# Tapestry scale benchmark — 50k entities / 100k relations

Recorded numbers for bundle assembly and SPA rendering at the visualization's
design target of 50,000 entities and 100,000 relations. Per the project's CI
policy, these are **reported benchmarks, not gates** — no wall-clock assertion
exists in CI or the test suite, and none should be added.

## Reproducing

The generator is checked in at `scripts/gen_bench_graph.py`; the benchmark
itself runs locally, never in CI:

```bash
docker compose up -d falkordb
uv run python scripts/gen_bench_graph.py --entities 50000 --relations 100000   # builds tapestry-bench
time uv run loom export-bundle '{"graph":"tapestry-bench"}' > /tmp/bench.json  # assembly time
ls -la /tmp/bench.json                                                         # bundle size
# SPA: render /tmp/bench.json into the template (as the e2e suite does), open
# it, and time initial render + interaction fps via Playwright tracing or a
# rAF fps meter. Record the numbers — do NOT add an assertion.
```

## Methodology

Measured once on the dev machine against a `tapestry-bench` graph built by
`scripts/gen_bench_graph.py --entities 50000 --relations 100000`:

- **Generator**: 50,000 entities in 92.3 s + 100,000 relations in 645.4 s =
  737.7 s total — dominated by relation writes (each relation write is its own
  graph mutation plus an event-log append; entity creation is comparatively
  cheap).
- **Bundle assembly**: `time uv run loom export-bundle
  '{"graph": "tapestry-bench"}'` for the analytics-on row; the same call with
  `{"include": {"analytics": false}}` for the analytics-off row. The
  analytics-on number reflects the guardrails firing as designed — betweenness
  omitted above `BETWEENNESS_MAX_NODES` (5,000 nodes) and loop enumeration
  skipped above `LOOP_MAX_NODES` (10,000 nodes), so only `degree` + `pagerank`
  ship in `centrality` and `loops` ships `[]`.
- **`visualize` HTML**: the same graph through `loom visualize` — build took
  23.3 s, producing a ~39.4 MB self-contained HTML file (the ~46.8 MB bundle
  JSON inlined into the template).
- **SPA initial render**: parse → `buildGraph` → first paint, timed via
  Playwright against the built 50k HTML file.
- **Interaction FPS**: sampled over 240 frames / 2002 ms with the layout frozen
  (post-settle) — zero stalls, zero console errors during the run.
- **Chronicle virtualization**: `ROW_HEIGHT` 46px, `OVERSCAN` 8, threshold
  `> 200` events (`VIRTUALIZE_THRESHOLD`); a 1,400-event synthetic timeline
  mounted 23–31 DOM rows at any scroll position — never the full list.
- **Frontend scale thresholds exercised**: Barnes-Hut + reduced sync-fallback
  iteration at `graph.order > 3000` (`barnesHutTheta` 0.6); label
  level-of-detail at `graph.order > 2000` (`labelRenderedSizeThreshold` 14).

## Measured vs. target

Targets were guidance, not gates:

| Metric | Target | Measured |
|---|---|---|
| Generator: 50k entities + 100k relations | — | 92.3 s + 645.4 s = 737.7 s total |
| Bundle assembly, 50k/100k, analytics on (guardrails active) | < ~15 s | 23.7 s |
| Bundle assembly, 50k/100k, `{"include":{"analytics":false}}` | < ~5 s | 6.6 s |
| Bundle size (JSON, analytics on) | — | ~46.8 MB |
| `visualize` HTML build / size | — | 23.3 s / ~39.4 MB |
| `betweenness` present in centrality at 50k | omitted (> 5k nodes) | omitted — `degree` + `pagerank` only |
| `loops` count at 50k | skipped (> 10k nodes) → `[]` | skipped → `[]` |
| SPA initial render (parse → buildGraph → first paint) | < ~5 s | 31.75 s |
| Interaction FPS, layout frozen | > 30 fps | 120 fps (240 frames / 2002 ms, zero stalls), zero console errors |
| Chronicle event list, 1,400 rows: scroll stays responsive | yes (virtualized) | yes — 23–31 DOM rows mounted (never all 1,400) |

## Honest caveats

Two numbers miss their aspirational targets, and the guardrails still did
their job:

- **Assembly-on, 23.7 s vs. < ~15 s target** — dominated by centrality at 50k
  nodes. Betweenness is correctly gated off above 5k nodes, but `degree` and
  `pagerank` still run in full, and pagerank's iterative solve over 100k edges
  is the largest remaining cost once betweenness is out of the picture.
- **SPA first paint, 31.75 s vs. < ~5 s target** — dominated by parsing a
  ~39 MB inline JSON bundle on the main thread, plus a Louvain clustering pass
  inside `buildGraph`; both scale with node/edge count in a single-file,
  no-server artifact that inlines its entire dataset.

Neither is a regression to fix — the frontend renders and stays interactive at
120 fps once loaded, and the honest fast-load path at this scale is the
`maxEntities` cap: a top-degree core with `meta.truncated` metadata trades
completeness for a bundle that assembles and parses quickly and
deterministically, at any graph size. If a re-measured number badly misses its
target, tune the thresholds (betweenness/loop node caps, label LOD, Barnes-Hut
theta) and re-record here — never by adding a timing assertion to CI.
