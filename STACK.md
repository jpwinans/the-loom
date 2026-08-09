# STACK.md — technology stack

The libraries The Loom depends on, and why. Versions/licenses were verified
against current (mid-2026) reality; re-verify before a long build if many months
have passed.

## Substrate — FalkorDB

The single store. Graph, entity vectors, and full-text indexes all live in one
FalkorDB instance, so hybrid search's three signals (vector, keyword, graph) all
read from one place with no reconcile layer. Its GraphBLAS (SuiteSparse) core is
the same semiring linear algebra the graph algebra builds on.

| Fact | Finding |
|---|---|
| Engine | GraphBLAS (SuiteSparse) — sparse adjacency-matrix graph |
| Named graphs | `db.select_graph('name')` — native multi-graph support |
| Cypher | OpenCypher + extensions |
| Vector index | `CREATE VECTOR INDEX … OPTIONS {dimension, similarityFunction}`, HNSW; query via `db.idx.vector.queryNodes` / `queryRelationships` |
| Full-text index | `db.idx.fulltext.createNodeIndex` / `queryNodes` (RediSearch heritage) |
| Python client | `falkordb` on PyPI (MIT) |
| License | Server is **SSPLv1** (source-available); the client is MIT |

**License note:** FalkorDB's server is SSPLv1, which is fine for a local/internal
tool that does not offer FalkorDB as a hosted service. It would only matter if
The Loom were resold as a managed service exposing FalkorDB. This project is ISC.

**Sources:** [FalkorDB](https://github.com/FalkorDB/FalkorDB) · [Vector Index docs](https://docs.falkordb.com/cypher/indexing/vector-index) · [Indexing docs](https://docs.falkordb.com/cypher/indexing.html)

## Algorithms & embeddings

| Library | License | Role |
|---|---|---|
| **rustworkx** (Qiskit) | Apache-2.0 | In-memory graph algorithms — PageRank (w/ personalization), simple cycles, VF2 mapping, betweenness centrality, Dijkstra, all-simple-paths, connected components, core numbers. |
| **python-graphblas** | Apache-2.0 | Semiring path algebra over SuiteSparse:GraphBLAS (tropical / max-times / plus-times, custom via Numba; `mxm`, `reduce_*`). |
| **fastembed** (Qdrant) | Apache-2.0 | ONNX in-process embeddings, no torch. `nomic-ai/nomic-embed-text-v1.5` (768-dim). |
| **scipy** / **POT** | BSD / MIT | Sparse numerics and optimal transport (far-analogy sliced-Wasserstein). |
| **numpy** | BSD-3-Clause | Dense array numerics beneath the embedding, extraction and projection paths — including the PCA projection that backs the visualization's Semantic Map. |

**Note on python-graphblas:** small community and infrequent releases. The
semirings used (Boolean/Tropical/Viterbi/Counting/Capacity) are small enough to
implement directly over `scipy.sparse` if it ever stalls.

**Sources:** [rustworkx](https://github.com/Qiskit/rustworkx) · [python-graphblas](https://github.com/python-graphblas/python-graphblas) · [fastembed](https://github.com/qdrant/fastembed)

## Documents & LLM

| Library | License | Role |
|---|---|---|
| **docling** (LF AI & Data) | MIT | Document ingestion — PDF/DOCX/PPTX/XLSX/HTML/MD/EPUB/images + structure (layout, tables, headings), OCR, exports to MD/HTML/JSON. |
| **instructor** | MIT | Pydantic-validated structured LLM outputs with auto-retry; Anthropic/Claude supported. |
| **anthropic** (Python SDK) | MIT | Claude API. |

**Sources:** [docling](https://github.com/docling-project/docling) · [instructor](https://github.com/instructor-ai/instructor)

## Toolchain & remaining libraries

| Library | Role |
|---|---|
| **uv** | dependencies / venv / lockfile / `uvx` distribution (commit `uv.lock`) |
| **Pydantic v2** | domain model, single source of truth (validates on load) |
| **Typer** | CLI framework (registry-driven command build) |
| **ruff / mypy --strict / pytest** | lint / types / tests — the green-main gate |
| **sympy** | symbolic math (imported natively) |
| **z3-solver** | optional CEGIS backend |
| **tree-sitter** (+ python/javascript/typescript/go/rust grammars) | codebase extraction |
| **fastapi** / **uvicorn** | optional extra `viz-serve` — the read-only live visualization server (`loom serve`) |
| **umap-learn** | optional extra `viz-umap` — alternative projection for the Semantic Map; PCA (numpy) is the default |

**Declared but unused:** `readability-lxml` (`pyproject.toml:41`) is a runtime
dependency with no import anywhere in the tree. It is a candidate for removal
rather than a library this project relies on; it is listed here so the gap
between the declared set and the used set stays visible.
