"""Type-constrained graph generator and the CEGIS type-compatibility graph.

The CEGIS foundation: the ``mulberry32`` PRNG plus ``TypeConstrainedGenerator``
and ``TypeCompatibilityGraph.createDefault()``.

The PRNG implements ``mulberry32`` (Math.imul semantics and the ``>>> 0`` unsigned
coercions reproduced with 32-bit masks) so a given seed yields a stable,
reproducible stream — the whole generator is deterministic given an explicit seed
(never a wall-clock seed; seeds are supplied by the caller).

``TypeCompatibilityGraph.createDefault()`` preserves *insertion* order, because
``getValidRelations`` returns relation types in insertion order and that order
drives which relation the seeded RNG selects for a pair.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from theloom.model import ALL_ENTITY_TYPES, CAUSAL_RELATION_TYPES

# =============================================================================
# mulberry32 — 32-bit PRNG
# =============================================================================

_MASK = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    """JS ``Math.imul``: 32-bit integer multiply keeping the low 32 bits.

    Bit patterns (not sign) are what the PRNG consumes, so masking both inputs
    and the product to unsigned 32 bits reproduces the signed int32 result of
    ``Math.imul`` exactly.
    """
    return ((a & _MASK) * (b & _MASK)) & _MASK


def mulberry32(seed: int) -> Callable[[], float]:
    """Return a mulberry32 generator producing the next float in ``[0, 1)``.

    The mulberry32 recurrence:
        s = (s + 0x6d2b79f5) | 0
        t = imul(s ^ (s >>> 15), 1 | s)
        t = (t + imul(t ^ (t >>> 7), 61 | t)) ^ t
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    """
    state = seed & _MASK

    def _next() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & _MASK
        t = state
        t = _imul(t ^ (t >> 15), t | 1)
        t = ((t + _imul(t ^ (t >> 7), t | 61)) & _MASK) ^ t
        return ((t ^ (t >> 14)) & _MASK) / 4294967296

    return _next


# =============================================================================
# Entity-type / causal-type / strength inventories (schema order)
# =============================================================================

# String values in schema order; the generator works in wire-string space so the
# type-graph keys ("source|relation|target") are exact string matches.
_ENTITY_TYPE_VALUES: list[str] = [t.value for t in ALL_ENTITY_TYPES]
_CAUSAL_VALUES: list[str] = [t.value for t in CAUSAL_RELATION_TYPES]

# The generator draws strength from exactly these three (not the
# ``foundational`` member of the Strength enum).
_STRENGTHS: list[str] = ["weak", "moderate", "strong"]


# =============================================================================
# Type compatibility graph
# =============================================================================


@dataclass(frozen=True)
class TypeTriple:
    """A valid (source type, relation type, target type) triple."""

    source_type: str
    relation_type: str
    target_type: str


class TypeCompatibilityGraph:
    """Encodes which (entity type, relation type, entity type) triples are valid.

    A ``set`` of serialized keys gives O(1) validity checks; a parallel list keeps
    insertion order for enumeration (which the seeded generator depends on).
    """

    def __init__(self) -> None:
        self._triples: set[str] = set()
        self._triple_list: list[TypeTriple] = []

    @staticmethod
    def _key(source: str, relation: str, target: str) -> str:
        return f"{source}|{relation}|{target}"

    def add_triple(self, source: str, relation: str, target: str) -> TypeCompatibilityGraph:
        key = self._key(source, relation, target)
        if key not in self._triples:
            self._triples.add(key)
            self._triple_list.append(TypeTriple(source, relation, target))
        return self

    def is_valid(self, source: str, relation: str, target: str) -> bool:
        return self._key(source, relation, target) in self._triples

    def get_valid_targets(self, source: str, relation: str) -> list[str]:
        return [
            t.target_type
            for t in self._triple_list
            if t.source_type == source and t.relation_type == relation
        ]

    def get_valid_sources(self, relation: str, target: str) -> list[str]:
        return [
            t.source_type
            for t in self._triple_list
            if t.relation_type == relation and t.target_type == target
        ]

    def get_valid_relations(self, source: str, target: str) -> list[str]:
        return [
            t.relation_type
            for t in self._triple_list
            if t.source_type == source and t.target_type == target
        ]

    def get_triples(self) -> list[TypeTriple]:
        return self._triple_list

    @classmethod
    def create_default(cls) -> TypeCompatibilityGraph:
        """The Loom's default schema.

        Insertion order is significant and is preserved exactly.
        """
        graph = cls()

        # Structural: related_to (any -> any)
        for src in _ENTITY_TYPE_VALUES:
            for tgt in _ENTITY_TYPE_VALUES:
                graph.add_triple(src, "related_to", tgt)
        # instance_of (any -> pattern)
        for src in _ENTITY_TYPE_VALUES:
            graph.add_triple(src, "instance_of", "pattern")
        # part_of (any -> system)
        for src in _ENTITY_TYPE_VALUES:
            graph.add_triple(src, "part_of", "system")
        # sources (any -> source)
        for src in _ENTITY_TYPE_VALUES:
            graph.add_triple(src, "sources", "source")

        # Epistemic: supports (evidence -> claim)
        graph.add_triple("evidence", "supports", "claim")
        # contradicts (any -> any)
        for src in _ENTITY_TYPE_VALUES:
            for tgt in _ENTITY_TYPE_VALUES:
                graph.add_triple(src, "contradicts", tgt)
        # questions (question -> any)
        for tgt in _ENTITY_TYPE_VALUES:
            graph.add_triple("question", "questions", tgt)
        # supersedes (same -> same)
        for same in _ENTITY_TYPE_VALUES:
            graph.add_triple(same, "supersedes", same)

        # Causal: connect variables, concepts, systems, and loops
        causal_entity_types = ["variable", "concept", "system", "loop"]
        for rel in _CAUSAL_VALUES:
            for src in causal_entity_types:
                for tgt in causal_entity_types:
                    graph.add_triple(src, rel, tgt)

        return graph


# =============================================================================
# Generation spec + result
# =============================================================================


@dataclass(frozen=True)
class GenerationSpec:
    """Bounds and requirements for constrained generation."""

    max_entities: int
    max_relations: int
    required_types: tuple[str, ...] = ()
    required_relations: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedEntity:
    name: str
    entity_type: str
    observations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GeneratedRelation:
    from_index: int
    to_index: int
    relation_type: str
    polarity: str | None
    strength: str


@dataclass
class GenerationResult:
    """Result of constrained generation (entities carry no id/timestamps)."""

    success: bool
    entities: list[GeneratedEntity]
    relations: list[GeneratedRelation]
    failure_reason: str | None = None


# =============================================================================
# TypeConstrainedGenerator
# =============================================================================


class TypeConstrainedGenerator:
    """Produce graph structures that satisfy all type-compatibility constraints.

    1. Validate that constraints are satisfiable (required types fit maxEntities).
    2. Generate required-type entities first, then fill with random valid types.
    3. Add required relations, then fill with random type-valid relations.
    4. Respect maxEntities/maxRelations; never self-loop or duplicate a relation.
    """

    def __init__(self, type_graph: TypeCompatibilityGraph) -> None:
        self._type_graph = type_graph

    def generate(self, spec: GenerationSpec, seed: int) -> GenerationResult:
        rng = mulberry32(seed)

        max_entities = spec.max_entities
        required_types = list(spec.required_types)
        required_relations = list(spec.required_relations)

        if max_entities == 0:
            if required_types:
                return GenerationResult(
                    False, [], [], "Cannot satisfy required types with maxEntities=0"
                )
            return GenerationResult(True, [], [])

        if len(required_types) > max_entities:
            return GenerationResult(
                False,
                [],
                [],
                f"Required {len(required_types)} types but maxEntities is {max_entities}",
            )

        for rel_type in required_relations:
            if not any(t.relation_type == rel_type for t in self._type_graph.get_triples()):
                return GenerationResult(
                    False,
                    [],
                    [],
                    f"Required relation '{rel_type}' has no valid triples in the type graph",
                )

        entities = self._generate_entities(max_entities, required_types, required_relations, rng)
        relations = self._generate_relations(entities, spec.max_relations, required_relations, rng)
        if relations is None:
            return GenerationResult(
                False, [], [], "Failed to generate relations satisfying constraints"
            )

        return GenerationResult(True, entities, relations)

    # -- entities -------------------------------------------------------------

    def _generate_entities(
        self,
        max_entities: int,
        required_types: list[str],
        required_relations: list[str],
        rng: Callable[[], float],
    ) -> list[GeneratedEntity]:
        entities: list[GeneratedEntity] = []

        for entity_type in required_types:
            entities.append(self._make_entity(entity_type))

        needed = self._types_needed_for_relations(
            required_relations, [e.entity_type for e in entities]
        )
        for entity_type in needed:
            if len(entities) >= max_entities:
                break
            entities.append(self._make_entity(entity_type))

        while len(entities) < max_entities:
            entity_type = _ENTITY_TYPE_VALUES[math.floor(rng() * len(_ENTITY_TYPE_VALUES))]
            entities.append(self._make_entity(entity_type))

        return entities

    @staticmethod
    def _make_entity(entity_type: str) -> GeneratedEntity:
        return GeneratedEntity(
            name=f"Generated {entity_type}",
            entity_type=entity_type,
            observations=[f"Auto-generated {entity_type} entity"],
        )

    def _types_needed_for_relations(
        self, required_relations: list[str], existing_types: list[str]
    ) -> list[str]:
        needed: list[str] = []
        existing = set(existing_types)

        for rel_type in required_relations:
            valid_triples = [
                t for t in self._type_graph.get_triples() if t.relation_type == rel_type
            ]
            if not valid_triples:
                continue

            has_source = any(t.source_type in existing for t in valid_triples)
            has_target = any(t.target_type in existing for t in valid_triples)

            if not has_source:
                source_type = valid_triples[0].source_type
                needed.append(source_type)
                existing.add(source_type)

            if not has_target:
                target_type = valid_triples[0].target_type
                if target_type not in existing:
                    needed.append(target_type)
                    existing.add(target_type)

        return needed

    # -- relations ------------------------------------------------------------

    def _generate_relations(
        self,
        entities: list[GeneratedEntity],
        max_relations: int,
        required_relations: list[str],
        rng: Callable[[], float],
    ) -> list[GeneratedRelation] | None:
        if max_relations == 0:
            return None if required_relations else []
        if len(entities) < 2:
            return None if required_relations else []

        relations: list[GeneratedRelation] = []
        used_pairs: set[str] = set()

        # Required relations first.
        for rel_type in required_relations:
            added = False
            for i in range(len(entities)):
                if added:
                    break
                for j in range(len(entities)):
                    if added:
                        break
                    if i == j:
                        continue
                    pair_key = f"{i}-{j}-{rel_type}"
                    if pair_key in used_pairs:
                        continue
                    if self._type_graph.is_valid(
                        entities[i].entity_type, rel_type, entities[j].entity_type
                    ):
                        polarity = self._assign_polarity(rel_type, rng)
                        strength = _STRENGTHS[math.floor(rng() * len(_STRENGTHS))]
                        relations.append(GeneratedRelation(i, j, rel_type, polarity, strength))
                        used_pairs.add(pair_key)
                        added = True
            if not added:
                return None

        # Fill remaining slots with random valid relations.
        max_attempts = max_relations * len(entities) * 3
        attempts = 0
        while len(relations) < max_relations and attempts < max_attempts:
            attempts += 1

            from_idx = math.floor(rng() * len(entities))
            to_idx = math.floor(rng() * len(entities))
            if to_idx == from_idx:
                continue

            source_type = entities[from_idx].entity_type
            target_type = entities[to_idx].entity_type

            valid_rels = self._type_graph.get_valid_relations(source_type, target_type)
            if not valid_rels:
                continue

            rel_type = valid_rels[math.floor(rng() * len(valid_rels))]
            pair_key = f"{from_idx}-{to_idx}-{rel_type}"
            if pair_key in used_pairs:
                continue

            polarity = self._assign_polarity(rel_type, rng)
            strength = _STRENGTHS[math.floor(rng() * len(_STRENGTHS))]
            relations.append(GeneratedRelation(from_idx, to_idx, rel_type, polarity, strength))
            used_pairs.add(pair_key)

        return relations

    @staticmethod
    def _assign_polarity(relation_type: str, rng: Callable[[], float]) -> str | None:
        if relation_type in _CAUSAL_VALUES:
            return "+" if rng() < 0.5 else "-"
        return None
