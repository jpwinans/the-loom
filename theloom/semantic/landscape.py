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
this embedder for this task shape, and (round 3 finding, below) so does the
naive fix of comparing a span against a handful of OTHER entity names: a
false friend's shared word inflates its similarity specifically to ITS OWN
entity, not just generically, so it "stands out from a random background"
almost as much as a genuine match does. This module answers "how related
does a genuine pair typically score, and where does unrelated content
typically sit" honestly; :func:`measure_specificity` below answers a
different, RELATIVE question that a caller like ``theloom.synthesis.fidelity``
needs on top of it.

=== Specificity: a relative decision, not a second absolute threshold ===

Round-2 fix (a residual-similarity guard: re-score with the shared word
stripped out) still failed round 3's fresh false friends live-tested against
the real embedder: even stripped of the shared word, a false friend's
residual span often still scored above the SAME global cutoff, because that
cutoff was calibrated against maximally topic-disjoint pairs whose variance
is too small to bound how high a coherent, on-topic-sounding sentence can
score by chance alone. Tightening it re-breaks recall on genuine paraphrases
(round 1's failure, reproduced). No single absolute number over this
embedder's raw similarity scale can separate the two classes — confirmed by
directly testing round 3's exact false-friend cases: "Root Cause Analysis"
scored ~0.52 against a definition with zero shared words AND against an
unrelated gardening sentence sharing only "root". The two are not
absolute-scale-separable.

:func:`measure_specificity` asks a RELATIVE question instead: not "is this
score high", but "is this score high FOR THIS ENTITY specifically" — an
entity's own baseline similarity to a battery of CLEARLY unrelated content
(the same document side as :data:`UNRELATED_PROBE_PAIRS`) is measured live,
per entity, and a candidate only counts as related when it clears that
entity's OWN baseline by a margin (a z-score) — not a fixed number of points
on the raw scale, but a number of THAT ENTITY'S OWN standard deviations. The
margin required (``specificity_z_cutoff``) is itself calibrated the same way
as ``meaningfully_related_cutoff`` above — :func:`_calibrate_cutoff`, reused
verbatim — just applied to z-scores computed from the corpus's own related
and unrelated pairs instead of raw scores. Live-measured: this DOES separate
round 3's false friends (z ≈ 0.5-1.2, using the residual/stripped text) from
the corpus's own genuine related pairs (z ≈ 2.5-11, one weak outlier) and
from named genuine cases (z ≈ 2.5-7.7) — see
theloom.synthesis.fidelity._semantic_grounding for how the two checks
(residual stripping + specificity z-score) compose.

The default (and primary) specificity representation embeds BOTH sides via
``embed_document``: the entity side gets a uniform ``"[concept] "`` type
anchor (:func:`entity_representation`) so a bare 2-3 word name isn't
embedded with nothing but the "short informational probe" prefix to lean
on. This representation empirically separates genuine-vs-coincidental
relatedness better than the old query/document asymmetry
(``measure_landscape`` keeps that asymmetry unchanged for its own cutoff —
desire 8's own reporting already passed independent review and is not this
module's problem to re-litigate). ``measure_specificity(..., representation=
"asymmetric")`` recomputes the same z-score machinery using that OLD
asymmetry instead (bare name via ``embed_query``, no type anchor); it
disagrees with the symmetric measurement often enough on different specific
cases that ``theloom.synthesis.fidelity`` cross-checks both rather than
trusting either alone — see that module's own docstring for the live
evidence.

=== Sense anchoring (round 4): identity is what an entity MEANS, not its name ===

Round 3's dual-representation z-score check (above) still failed FRESH false
friends live-tested against the real embedder — "Hot Take" against an
unrelated soup sentence, "Silver Bullet Solution" against a werewolf-hunting
sentence, and others, all cleared BOTH cutoffs. The pattern across three
rounds of live evidence: every design so far — absolute cutoff, residual
stripping, per-entity z-score, two independent representations — anchored
the entity side ONLY on its NAME. A false-friend sentence contains the
name's own words by construction, so name-vs-span similarity stays elevated
in EVERY representation and EVERY normalization tried; the name alone never
carries the information needed to tell "about this entity" apart from
"contains this entity's word".

That information exists elsewhere: an entity's OWN observations (The Loom
requires at least one at creation — ``guards.entity_gate_warnings``) are its
definition. Round 4's first attempt at using it (below, kept for the
history) still failed; round 5's fix (further below) is the current design.

Round 4 (superseded): built "Name: obs1. obs2." — a dictionary entry
including the name — and compared it against the candidate SPAN with the
entity's own significant words stripped out first, the same residual guard
the name-based checks already used. This does NOT replace
:func:`entity_representation`'s bare-name representations — an entity
without meaningful observations has no sense anchor to build (see
``theloom.synthesis.fidelity``'s guard-placeholder filter) and must fall
back to them, disclosed as a degraded basis. That part of the design still
holds; the anchor's own shape did not survive round 5 (below).

=== Round 5: the one-sided cut — cut the ANCHOR's name, not the SPAN ===

Round 4's anchor still lost to a fresh critic: word-SHARING GENUINE
mentions (a paraphrase that legitimately reuses one of the entity's own
words, including reusing a full idiomatic phrase — "the elephant in the
room" said about the actual idiom, not a zoo animal) were being rejected
right along with the false friends they were supposed to be told apart
from. Stripping the shared word out of the SPAN removes the entity's own
vocabulary from that side of the comparison regardless of whether that
vocabulary was a coincidental trap or the genuine mention's own real
content — a false friend's stripped residual and a genuine word-sharing
mention's stripped residual ended up statistically indistinguishable
(live-measured: overlapping z-bands, roughly 1.14-3.15 for wrongly-rejected
genuine mentions vs. roughly 1.11-2.87 for correctly-rejected false
friends against round 4's cutoff) because stripping erased the
discriminating signal on both classes evenly.

The shared-word channel that inflates similarity exists on BOTH sides of a
name-vs-span comparison: the entity's own name, and the span that (by
construction, in the trap this whole check exists for) also contains it.
Only ONE side needs to be cut to break the channel. :func:`observation_anchor`
builds the anchor from the entity's observations ALONE — no name, and
structurally no way to reintroduce one by mistake: the function does not
accept a ``name`` parameter at all. The candidate span is then compared
INTACT, never stripped, against that anchor. A false friend's span still
contains the entity's name-shaped words, but the anchor no longer contains
them either, so their literal overlap no longer inflates the comparison;
what is left on both sides is genuine semantic content, exactly the
question this check exists to ask. A genuine word-sharing mention keeps its
own real meaning completely intact, including when it reuses the entity's
name as part of a full idiomatic phrase.

:data:`SENSE_ANCHOR_PROBE_PAIRS` was rebuilt to match: every false-friend
and related document is now the INTACT sentence (never pre-stripped), and
several related documents deliberately reuse a significant word from their
entity's name — some as a full-phrase idiom ("a silver bullet solution",
"the elephant in the room") — matching the actual shape of the case this
cutoff has to separate: two documents that both contain the entity's own
words, one meaning it and one not. The z-cutoff is still derived live from
:func:`_calibrate_cutoff` against whatever this corpus currently measures;
editing the corpus changes the next measurement, same guarantee as
everywhere else in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from statistics import pstdev
from typing import Any, Literal, Protocol, cast

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


# =============================================================================
# Specificity: a RELATIVE (per-entity, z-scored) decision. See the module
# docstring's "Specificity" section for why this exists alongside (not
# instead of) measure_landscape above.
# =============================================================================


def entity_representation(name: str) -> str:
    """The text embedded for an entity's name side of a specificity check —
    a uniform ``"[concept] "`` type anchor, not the entity's own real type.
    Calibration (below) and application (theloom.synthesis.fidelity) must
    embed the name the same way for the calibrated z-cutoff to mean the same
    thing in both places; using each entity's real type would require
    re-deriving the cutoff per type, for a benefit (a marginally sharper
    anchor) this module has not measured to be worth that cost.
    """
    return f"[concept] {name}"


def unrelated_document_battery(
    unrelated_pairs: tuple[tuple[str, str], ...] | None = None,
) -> tuple[str, ...]:
    """The document side of the (live, current) unrelated-pair corpus — a
    battery of clearly-unrelated content used as each entity's own null
    baseline. Reads the CURRENT module-level corpus by default, so a test
    (or a future edit to :data:`UNRELATED_PROBE_PAIRS`) changes this too.
    """
    pairs = unrelated_pairs if unrelated_pairs is not None else UNRELATED_PROBE_PAIRS
    return tuple(document for _, document in pairs)


def observation_anchor(observations: list[str]) -> str:
    """An entity's OWN definition as embeddable text, built from its
    observations ALONE — no name, no reference to the entity's own words at
    all. There is deliberately no ``name`` parameter: round 4's anchor took
    one and interpolated it into the text ("Name: obs1. obs2."), which put
    the entity's own words back on the anchor side of the comparison and
    caused round 5's regression (see the module docstring's "the one-sided
    cut" section). Structurally omitting the parameter means there is no way
    to reintroduce that channel by accident. Returns ``""`` when there are no
    observations to anchor with; callers decide whether "no observations"
    should even reach here (theloom.synthesis.fidelity filters the
    OBSERVATIONS_REQUIRED guard placeholder before calling this, and falls
    back to :func:`entity_representation`'s name-based check instead of
    calling this with nothing to work with).
    """
    joined = ". ".join(o.rstrip(". ") for o in observations if o.strip())
    return f"{joined}." if joined else ""


# Calibration pairs for the "sense" specificity representation: each entry is
# (name, observations, false_friend_document, related_document). Both
# documents are INTACT — never pre-stripped (round 5; see the module
# docstring's "one-sided cut" section for why stripping the SPAN was the
# defect, not a safeguard) — and several related documents deliberately
# reuse a significant word from their entity's name, several as a
# full-phrase idiom, matching the actual shape of what this cutoff has to
# separate at decision time: two documents that both contain the entity's
# own words, one meaning it and one not. None of these pairs, or their
# variants, appear in theloom.synthesis.fidelity's own test suite, so
# calibration and validation stay independent.
SENSE_ANCHOR_PROBE_PAIRS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "Hot Take",
        (
            "a deliberately provocative or contrarian opinion expressed quickly "
            "without much reflection",
        ),
        "She burned her tongue on the hot soup during dinner.",
        ("his hot take on the trade got picked apart by fans within minutes of the announcement"),
    ),
    (
        "Anchor Tenant",
        ("a major, well-known store that draws customer traffic to a shopping center",),
        "The old sailor spent the whole afternoon lowering the ship's heavy anchor into the bay.",
        (
            "the anchor tenant at the new mall pulls in shoppers for every "
            "smaller retailer around it"
        ),
    ),
    (
        "Sunk Cost Fallacy",
        (
            "continuing to invest in a decision because of resources already spent "
            "rather than future value",
        ),
        "The rusted shipwreck had sunk in the harbor decades before anyone thought to raise it.",
        (
            "he kept funding the doomed project purely out of sunk cost, unable "
            "to accept what was already spent"
        ),
    ),
    (
        "Low Hanging Fruit Strategy",
        ("prioritizing the easiest, most accessible wins before tackling harder problems",),
        "The children spent the afternoon picking fruit from the orchard trees.",
        (
            "the team grabbed the low hanging fruit first, knocking out the easy "
            "wins before anything harder"
        ),
    ),
    (
        "Silver Bullet Solution",
        ("a single simple fix believed to solve a complex problem completely",),
        "The werewolf hunter loaded a single silver bullet before entering the moonlit forest.",
        (
            "everyone hoped the new framework would be the silver bullet solution "
            "that fixed everything overnight"
        ),
    ),
    (
        "Boiling Point Threshold",
        ("the point at which accumulated pressure or frustration causes a sudden reaction",),
        (
            "The chemist recorded the exact boiling point of the unknown liquid "
            "sample in her notebook."
        ),
        "months of built-up frustration finally reached its boiling point during the meeting",
    ),
    (
        "Elephant In The Room",
        ("an obvious problem or difficult topic that everyone is avoiding discussing",),
        "The zoo's newest baby elephant delighted children gathered in the same viewing room.",
        (
            "nobody wanted to be the one to mention the elephant in the room "
            "during the layoffs meeting"
        ),
    ),
    (
        "Rubber Stamp",
        ("approving something automatically without real scrutiny or independent judgment",),
        "The office supply store restocked ink pads and a rubber stamp for the librarian's desk.",
        (
            "the board treated every proposal as a rubber stamp, approving it "
            "without asking a single question"
        ),
    ),
)


@dataclass(frozen=True)
class SpecificityProfile:
    """How many of an entity's OWN standard deviations above ITS OWN
    unrelated-content baseline a candidate needs to clear to count as
    specifically about that entity — calibrated from the same probe corpus
    :func:`measure_landscape` uses, transformed onto the z-score scale."""

    unrelated_z_baseline: BandStats
    related_z_range: BandStats
    specificity_z_cutoff: float
    cutoff_method: str


SpecificityRepresentation = Literal["symmetric", "asymmetric"]

# How the entity-NAME side is embedded — the document side (spans, probe
# corpus documents) is always embed_document either way. "symmetric" is
# measure_specificity's default and does most of the work (see the module
# docstring); "asymmetric" (the bare name through embed_query, no type
# anchor — the OLD round-1/2 representation) independently disagrees with
# it often enough on different cases to be worth cross-checking: live
# evidence (theloom.synthesis.fidelity, round 3) is that requiring BOTH
# representations to agree catches false friends neither one catches alone,
# without costing recall on genuine matches (asymmetric z-scores run far
# higher for genuine content, so symmetric stays the binding constraint
# there) — two independently-calibrated, principled measurements
# cross-checking each other, not a second free parameter tuned to specific
# examples.
_NAME_VECTOR_FNS: dict[SpecificityRepresentation, Any] = {
    "symmetric": lambda embedder, name: embedder.embed_document(entity_representation(name)),
    "asymmetric": lambda embedder, name: embedder.embed_query(name),
}


def _name_baseline(
    embedder: SupportsLandscapeEmbedding,
    name: str,
    unrelated_docs: tuple[str, ...],
    doc_vectors: dict[str, list[float]],
    name_vector_fn: Any,
    *,
    exclude_doc: str | None = None,
) -> tuple[float, float, list[float]]:
    """``name``'s own (mean, stdev, name_vector) against ``unrelated_docs``
    — its personal noise floor. ``exclude_doc`` leaves one document out of
    the battery (used when that same document IS the candidate being
    scored, in :func:`_measure_specificity`'s own unrelated-pair pass, so a
    document is never measured against a baseline that already includes it)."""
    name_vector = name_vector_fn(embedder, name)
    docs = [d for d in unrelated_docs if d != exclude_doc]
    scores = [l2_similarity(cosine_similarity(name_vector, doc_vectors[d])) for d in docs]
    mean = sum(scores) / len(scores)
    stdev = pstdev(scores) if len(scores) > 1 else 0.0
    return mean, stdev, name_vector


def _measure_specificity(
    embedder: SupportsLandscapeEmbedding,
    unrelated_pairs: tuple[tuple[str, str], ...],
    related_pairs: tuple[tuple[str, str], ...],
    representation: SpecificityRepresentation,
) -> SpecificityProfile:
    name_vector_fn = _NAME_VECTOR_FNS[representation]
    unrelated_docs = unrelated_document_battery(unrelated_pairs)
    doc_vectors = {document: embedder.embed_document(document) for document in set(unrelated_docs)}

    related_zs: list[float] = []
    for name, document in related_pairs:
        mean, stdev, name_vector = _name_baseline(
            embedder, name, unrelated_docs, doc_vectors, name_vector_fn
        )
        actual = l2_similarity(cosine_similarity(name_vector, embedder.embed_document(document)))
        related_zs.append((actual - mean) / stdev if stdev > 0 else 0.0)

    unrelated_zs: list[float] = []
    for name, document in unrelated_pairs:
        # Leave-one-out: this document must not be part of its own baseline.
        mean, stdev, name_vector = _name_baseline(
            embedder, name, unrelated_docs, doc_vectors, name_vector_fn, exclude_doc=document
        )
        actual = l2_similarity(cosine_similarity(name_vector, doc_vectors[document]))
        unrelated_zs.append((actual - mean) / stdev if stdev > 0 else 0.0)

    unrelated_band = _band_stats(unrelated_zs)
    related_band = _band_stats(related_zs)
    cutoff, method = _calibrate_cutoff(unrelated_band, related_band)
    return SpecificityProfile(unrelated_band, related_band, cutoff, method)


@lru_cache(maxsize=16)
def _measure_specificity_cached(
    embedder: Any,
    unrelated_pairs: tuple[tuple[str, str], ...],
    related_pairs: tuple[tuple[str, str], ...],
    representation: SpecificityRepresentation,
) -> SpecificityProfile:
    # See _measure_cached's own comment on the Any/Hashable tension.
    return _measure_specificity(embedder, unrelated_pairs, related_pairs, representation)


def measure_specificity(
    embedder: SupportsLandscapeEmbedding,
    *,
    unrelated_pairs: tuple[tuple[str, str], ...] | None = None,
    related_pairs: tuple[tuple[str, str], ...] | None = None,
    representation: SpecificityRepresentation = "symmetric",
    use_cache: bool = True,
) -> SpecificityProfile:
    """Measure the z-score margin that separates "specifically related to
    this entity" from "generically similar to any entity" on ``embedder``.

    ``representation`` selects how the entity-name side is embedded — see
    ``_NAME_VECTOR_FNS``'s comment for why callers cross-check both.
    Live by default, same caching contract as :func:`measure_landscape`.
    """
    unrelated = unrelated_pairs if unrelated_pairs is not None else UNRELATED_PROBE_PAIRS
    related = related_pairs if related_pairs is not None else RELATED_PROBE_PAIRS
    if not use_cache:
        return _measure_specificity(embedder, unrelated, related, representation)
    return _measure_specificity_cached(cast(Any, embedder), unrelated, related, representation)


# =============================================================================
# Sense specificity: measure_specificity's z-score machinery, applied to
# theloom.semantic.landscape.observation_anchor's observations-only anchors
# instead of bare (or type-anchored) names. A dedicated function rather than
# a third ``representation`` value on measure_specificity:
# :data:`SENSE_ANCHOR_PROBE_PAIRS` carries observations and an intact
# false-friend document per entry (4-tuples), a genuinely different shape
# from measure_specificity's (name, document) 2-tuples, not just a
# different name-embedding function.
# =============================================================================


def _sense_baseline(
    embedder: SupportsLandscapeEmbedding, anchor: str, battery_vectors: dict[str, list[float]]
) -> tuple[float, float, list[float]]:
    anchor_vector = embedder.embed_document(anchor)
    scores = [l2_similarity(cosine_similarity(anchor_vector, v)) for v in battery_vectors.values()]
    mean = sum(scores) / len(scores)
    stdev = pstdev(scores) if len(scores) > 1 else 0.0
    return mean, stdev, anchor_vector


def _measure_sense_specificity(
    embedder: SupportsLandscapeEmbedding,
    pairs: tuple[tuple[str, tuple[str, ...], str, str], ...],
    unrelated_pairs: tuple[tuple[str, str], ...],
) -> SpecificityProfile:
    battery_docs = unrelated_document_battery(unrelated_pairs)
    battery_vectors = {
        document: embedder.embed_document(document) for document in set(battery_docs)
    }

    unrelated_zs: list[float] = []
    related_zs: list[float] = []
    for _name, observations, false_friend_document, related_document in pairs:
        # _name is not used to build the anchor -- see observation_anchor's
        # own docstring for why that omission is structural, not incidental.
        anchor = observation_anchor(list(observations))
        mean, stdev, anchor_vector = _sense_baseline(embedder, anchor, battery_vectors)
        false_friend_score = l2_similarity(
            cosine_similarity(anchor_vector, embedder.embed_document(false_friend_document))
        )
        related_score = l2_similarity(
            cosine_similarity(anchor_vector, embedder.embed_document(related_document))
        )
        unrelated_zs.append((false_friend_score - mean) / stdev if stdev > 0 else 0.0)
        related_zs.append((related_score - mean) / stdev if stdev > 0 else 0.0)

    unrelated_band = _band_stats(unrelated_zs)
    related_band = _band_stats(related_zs)
    cutoff, method = _calibrate_cutoff(unrelated_band, related_band)
    return SpecificityProfile(unrelated_band, related_band, cutoff, method)


@lru_cache(maxsize=8)
def _measure_sense_specificity_cached(
    embedder: Any,
    pairs: tuple[tuple[str, tuple[str, ...], str, str], ...],
    unrelated_pairs: tuple[tuple[str, str], ...],
) -> SpecificityProfile:
    return _measure_sense_specificity(embedder, pairs, unrelated_pairs)


def measure_sense_specificity(
    embedder: SupportsLandscapeEmbedding,
    *,
    pairs: tuple[tuple[str, tuple[str, ...], str, str], ...] | None = None,
    unrelated_pairs: tuple[tuple[str, str], ...] | None = None,
    use_cache: bool = True,
) -> SpecificityProfile:
    """Measure the z-score margin for sense-anchored (observations-only,
    round 5) comparisons — see the module docstring's "one-sided cut"
    section.

    Live by default, same caching contract as :func:`measure_landscape`;
    edit :data:`SENSE_ANCHOR_PROBE_PAIRS` and the next measurement reports
    different numbers, the same "not a fresh magic number" guarantee as
    every other calibration in this module.
    """
    resolved_pairs = pairs if pairs is not None else SENSE_ANCHOR_PROBE_PAIRS
    resolved_unrelated = unrelated_pairs if unrelated_pairs is not None else UNRELATED_PROBE_PAIRS
    if not use_cache:
        return _measure_sense_specificity(embedder, resolved_pairs, resolved_unrelated)
    return _measure_sense_specificity_cached(
        cast(Any, embedder), resolved_pairs, resolved_unrelated
    )
