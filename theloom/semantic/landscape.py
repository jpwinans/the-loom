"""The embedder's own empirical similarity landscape, measured live.

Every threshold anyone picks for "meaningfully related" is a guess about the
geometry of a specific embedding model. The textbook guess — cosine 0, two
orthogonal vectors, no linear relationship at all — is not a safe one: real
embedding models are anisotropic (their embeddings occupy a narrow cone
rather than spanning the full sphere), so two genuinely UNRELATED texts
routinely score well above 0. ``theloom.operations.synthesis`` already
learned this the hard way and hand-measured one number once
(``VERIFY_FIDELITY_RELEVANCE_FLOOR``, cosine ≈ 0.41-0.44 observed against
this repo's own local embedder). That number is frozen in a source-code
comment; if the model, the fastembed build, or the ONNX runtime ever changes
underneath it, nothing re-checks it.

This module is the substrate saying that geometry out loud, live, every time
it is asked, against a small fixed probe corpus:

- :data:`UNRELATED_PROBE_PAIRS` — pairs of texts drawn from disjoint topical
  domains that share no real content. Their scores are the embedder's
  general anisotropic noise floor.
- :data:`RELATED_PROBE_PAIRS` — a short, entity-name-shaped phrase paired
  with a longer paraphrase or definition of the *same* concept, spanning
  tight restatements to loose thematic kinship. Their scores are what
  genuine relatedness actually looks like on this model.

:func:`measure_landscape` embeds every pair through the CALLER-SUPPLIED
embedder (never a module-global one — there is no second embedder-resolution
path here, callers pass whatever ``get_embedder()`` gave them) and reports
the observed bands plus a cutoff calibrated from those observations, never a
literal. Edit either probe list and the next measurement reports different
numbers — there is no cached, hand-typed number anywhere in this module.

Both sides of a pair are embedded asymmetrically, matching how a caller like
``verify-fidelity`` actually uses this geometry: the short, name-like side
through ``embed_query`` (nomic's "short informational probe" prefix), the
longer, passage-like side through ``embed_document`` — the same asymmetry
:mod:`theloom.semantic.search` uses for retrieval, so a cutoff measured here
is valid on the same shared ``1/(1+L2)`` score scale everything else in this
package reports on.

A note on what this cutoff is NOT good for on its own: this repo's local
embedder inflates similarity almost independent of true relatedness whenever
a candidate shares a literal word or stem with the query — a coincidental
"false friend" (a wine decanter's *bottleneck*, not a throughput constraint)
can score as high as a weak genuine paraphrase. Live-measured proof: two
pairs sharing a word/stem but meaning something else entirely both cleared
this module's own cutoff (``Bottleneck``/"the narrow bottleneck of the wine
decanter made pouring slow" and ``Onboarding Friction``/"the new hire's
onboarding paperwork took all morning to finish", scored against the probe
corpus below). A single scalar threshold over a mixed corpus cannot both
accept most genuine (often word-sharing) paraphrases and reject every
coincidental word-sharing false friend — those two distributions overlap on
this embedder for this task shape. This module answers "how related does a
genuine pair typically score, and where does unrelated content typically
sit" honestly; a caller that also needs to distinguish a false friend from a
real match (``theloom.synthesis.fidelity``) layers an additional check on
top rather than asking this module for a single number that can't exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from statistics import pstdev
from typing import Any, Protocol, cast

from theloom.semantic.embed import cosine_similarity
from theloom.semantic.search import l2_similarity

Doc = dict[str, Any]


class SupportsLandscapeEmbedding(Protocol):
    """The slice of the embedder this module needs."""

    def embed_query(self, text: str) -> list[float]: ...

    def embed_document(self, text: str) -> list[float]: ...


# =============================================================================
# The probe corpus — plain data, inspectable and editable in place. Changing
# either list changes what the next measurement reports; nothing downstream
# hard-codes a count or a value from it.
#
# UNRELATED_PROBE_PAIRS is topically disjoint pairs only (no shared
# vocabulary at all) — the general "how high can pure noise score" question.
# It deliberately does NOT try to also encode the "false friend" (shared
# word, wrong sense) failure mode: mixing that in inflates this band's
# variance enough to push the cutoff above most genuine (often word-sharing)
# paraphrases, trading a recall collapse on RELATED_PROBE_PAIRS for a
# precision gain against an adversarial case that a single scalar threshold
# can't cleanly separate from real matches anyway (see the module docstring).
# ``theloom.synthesis.fidelity`` handles false friends with a second,
# targeted check instead of asking this corpus to do double duty.
# =============================================================================

UNRELATED_PROBE_PAIRS: tuple[tuple[str, str], ...] = (
    ("Feedback Delay", "a sourdough starter's hydration ratio for baking bread"),
    ("Supply Chain Bottleneck", "the migratory flight pattern of arctic terns"),
    ("Leverage Point", "the plot of a nineteenth-century opera about a shipwreck"),
    ("Confirmation Bias", "how to reglaze a cracked stained-glass window pane"),
    ("Carrying Capacity", "a recipe for a three-layer chocolate cake"),
    ("Reinforcing Loop", "the house rules of a regional dominoes variant"),
    ("Technical Debt", "the geology of limestone caves in a national park"),
    ("Market Saturation", "the overwintering habits of monarch butterflies"),
    ("Groupthink", "the tuning process for a twelve-string guitar"),
    ("Bottleneck", "a watercolor technique for painting cloud reflections"),
    ("Feedback Loop", "the history of Byzantine mosaic tilework"),
    ("Onboarding Friction", "the anatomy of a deep-sea anglerfish"),
    ("Escalation Of Commitment", "a walking tour of medieval cathedral architecture"),
    ("Thermostat", "the etiquette of a formal tea ceremony in Kyoto"),
)

RELATED_PROBE_PAIRS: tuple[tuple[str, str], ...] = (
    ("Feedback Delay", "the lag in the feedback loop before the correction lands"),
    ("Supply Chain Bottleneck", "a constrained link throttling the flow of goods downstream"),
    ("Thermostat", "a device that regulates temperature by cycling a heater on and off"),
    ("Leverage Point", "a place in a system where a small shift produces a large effect"),
    (
        "Confirmation Bias",
        "the tendency to favor information that confirms what you already believe",
    ),
    ("Carrying Capacity", "the maximum population an environment can sustain indefinitely"),
    ("Reinforcing Loop", "a cycle that amplifies its own initial change with each pass"),
    ("Technical Debt", "shortcuts taken now that cost more time to fix later"),
    ("Market Saturation", "the point where demand for a product stops growing"),
    ("Groupthink", "a group's tendency to reach consensus without critically evaluating it"),
    ("Bottleneck", "the slowest step that limits the throughput of the whole process"),
    ("Feedback Loop", "output routed back around to become input again"),
    ("Onboarding Friction", "the obstacles new users hit when first adopting a product"),
    (
        "Escalation Of Commitment",
        "continuing to invest in a failing plan because of what was already spent",
    ),
)

DEFAULT_MIN_SEPARATION_STDEVS = 2.0


@dataclass(frozen=True)
class PairMeasurement:
    """One probe pair's live-measured score."""

    query: str
    document: str
    relation: str  # "unrelated" | "related"
    cosine: float
    score: float  # 1/(1+L2) on the shared scale (see theloom.semantic.search)


@dataclass(frozen=True)
class BandStats:
    """Summary statistics for one side of the landscape, on the score scale."""

    mean: float
    min: float
    max: float
    stdev: float
    sample_size: int


@dataclass(frozen=True)
class LandscapeProfile:
    """A live measurement of one embedder's similarity landscape."""

    pairs: tuple[PairMeasurement, ...]
    unrelated_baseline: BandStats
    related_range: BandStats
    meaningfully_related_cutoff: float
    cutoff_method: str


def _band_stats(scores: list[float]) -> BandStats:
    return BandStats(
        mean=sum(scores) / len(scores),
        min=min(scores),
        max=max(scores),
        stdev=pstdev(scores) if len(scores) > 1 else 0.0,
        sample_size=len(scores),
    )


def _score_pair(
    embedder: SupportsLandscapeEmbedding, query: str, document: str
) -> tuple[float, float]:
    cosine = cosine_similarity(embedder.embed_query(query), embedder.embed_document(document))
    return cosine, l2_similarity(cosine)


def _calibrate_cutoff(unrelated: BandStats, related: BandStats) -> tuple[float, str]:
    """Pick the cutoff from what was just observed, never from a literal.

    When the two bands are cleanly separated (every related pair scored
    above every unrelated pair), the cutoff sits at the midpoint of the gap
    — comfortably clear of both the noisiest unrelated pair and the weakest
    related one. When the probe corpus produced overlapping bands (an
    unrelated pair scored as high as, or higher than, the weakest related
    pair — entirely possible with a small corpus or an unusual model), there
    is no gap to split: the cutoff falls back to
    ``DEFAULT_MIN_SEPARATION_STDEVS`` standard deviations above the
    unrelated mean, and the method string discloses that the corpus gave no
    clean separation so a caller can judge the cutoff's reliability instead
    of trusting it blindly.
    """
    if related.min > unrelated.max:
        cutoff = (unrelated.max + related.min) / 2
        method = (
            "midpoint between the observed unrelated-pair ceiling "
            f"({unrelated.max:.4f}) and the observed related-pair floor "
            f"({related.min:.4f}) — the probe corpus separated cleanly"
        )
        return cutoff, method
    cutoff = unrelated.mean + DEFAULT_MIN_SEPARATION_STDEVS * unrelated.stdev
    method = (
        f"{DEFAULT_MIN_SEPARATION_STDEVS:g} standard deviations above the observed "
        f"unrelated-pair mean ({unrelated.mean:.4f} + {DEFAULT_MIN_SEPARATION_STDEVS:g}"
        f"×{unrelated.stdev:.4f}) — the probe corpus's unrelated and related bands "
        f"overlapped (unrelated max {unrelated.max:.4f} >= related min {related.min:.4f}), "
        "so there was no clean gap to split"
    )
    return cutoff, method


def _measure(
    embedder: SupportsLandscapeEmbedding,
    unrelated_pairs: tuple[tuple[str, str], ...],
    related_pairs: tuple[tuple[str, str], ...],
) -> LandscapeProfile:
    measurements: list[PairMeasurement] = []
    unrelated_scores: list[float] = []
    for query, document in unrelated_pairs:
        cosine, score = _score_pair(embedder, query, document)
        unrelated_scores.append(score)
        measurements.append(PairMeasurement(query, document, "unrelated", cosine, score))
    related_scores: list[float] = []
    for query, document in related_pairs:
        cosine, score = _score_pair(embedder, query, document)
        related_scores.append(score)
        measurements.append(PairMeasurement(query, document, "related", cosine, score))

    unrelated_baseline = _band_stats(unrelated_scores)
    related_range = _band_stats(related_scores)
    cutoff, method = _calibrate_cutoff(unrelated_baseline, related_range)
    return LandscapeProfile(
        pairs=tuple(measurements),
        unrelated_baseline=unrelated_baseline,
        related_range=related_range,
        meaningfully_related_cutoff=cutoff,
        cutoff_method=method,
    )


@lru_cache(maxsize=8)
def _measure_cached(
    embedder: Any,
    unrelated_pairs: tuple[tuple[str, str], ...],
    related_pairs: tuple[tuple[str, str], ...],
) -> LandscapeProfile:
    # ``embedder`` is typed ``Any`` here (not ``SupportsLandscapeEmbedding``)
    # solely because ``lru_cache`` requires ``Hashable`` and a structural
    # Protocol isn't statically known to be hashable — every real caller
    # still passes something satisfying the protocol, checked at the
    # ``_measure`` call below.
    return _measure(embedder, unrelated_pairs, related_pairs)


def measure_landscape(
    embedder: SupportsLandscapeEmbedding,
    *,
    unrelated_pairs: tuple[tuple[str, str], ...] | None = None,
    related_pairs: tuple[tuple[str, str], ...] | None = None,
    use_cache: bool = True,
) -> LandscapeProfile:
    """Measure ``embedder``'s similarity landscape against the probe corpus.

    Live by default: every call re-embeds the full corpus through
    ``embedder``. ``use_cache`` (on by default) memoizes the result for the
    lifetime of this process, keyed on the embedder object's identity and
    the exact probe corpus used — a one-shot CLI invocation still measures
    live (a fresh process is a cold cache), but a single invocation that
    asks twice (``embedder-profile`` composed with a fidelity check, say)
    does not pay for the corpus twice. Pass ``use_cache=False`` to force a
    fresh measurement regardless.
    """
    unrelated = unrelated_pairs if unrelated_pairs is not None else UNRELATED_PROBE_PAIRS
    related = related_pairs if related_pairs is not None else RELATED_PROBE_PAIRS
    if not use_cache:
        return _measure(embedder, unrelated, related)
    # See _measure_cached's own comment: lru_cache needs Hashable, which a
    # structural Protocol is never statically known to satisfy.
    return _measure_cached(cast(Any, embedder), unrelated, related)


def band_stats_doc(stats: BandStats) -> Doc:
    return {
        "meanScore": stats.mean,
        "minScore": stats.min,
        "maxScore": stats.max,
        "stdevScore": stats.stdev,
        "sampleSize": stats.sample_size,
    }


def pair_doc(pair: PairMeasurement) -> Doc:
    return {
        "query": pair.query,
        "document": pair.document,
        "relation": pair.relation,
        "cosine": pair.cosine,
        "score": pair.score,
    }
