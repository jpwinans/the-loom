"""Golden test pinning reify-patterns' WL fingerprint output.

Guards against drift between the inline fingerprint hashing in
`theloom.operations.reification` (reify-patterns) and the extracted,
reusable `theloom.reification.fingerprint` module. This test's expected
hash is computed by hand from the documented canonical-string algorithm
(sha256(...)[:16] over "<entityType>|in:...|out:...|neighbors:..." at
depth 1, folded with sorted neighbor hashes at deeper depths) and must
keep passing byte-for-byte once reify-patterns is switched to call the
shared module.
"""

from __future__ import annotations

from theloom.model import EntityCreate, RelationCreate
from theloom.operations.reification import ReifyPatternsInput, reify_patterns
from theloom.store.multigraph import MultiGraph

# Hand-computed via hashlib.sha256(...).hexdigest()[:16]:
#   depth1 = sha256("concept|in:|out:|neighbors:")[:16]
#   depth2 = sha256(f"{depth1}|")[:16]
_EXPECTED_DEPTH1_HASH = "1520bf3922b142b8"
_EXPECTED_DEPTH2_HASH = "39c203a283d6a407"

# Edgeful fixture, hand-computed the same way. Each hub is a claim with two
# incoming `supports` edges from distinct evidence nodes and one outgoing
# `references` edge to a concept, so the depth-1 canonical keeps per-edge
# duplicates ("in:supports,supports") while neighbor types dedup by node id
# (both evidence nodes present, as distinct nodes), and the depth-2 fold
# keeps both evidence hashes (identical values from distinct neighbors):
#   h1_hub = sha256("claim|in:supports,supports|out:references"
#                   "|neighbors:concept,evidence,evidence")[:16]
#   h1_src = sha256("evidence|in:|out:supports|neighbors:claim")[:16]
#   h1_tgt = sha256("concept|in:references|out:|neighbors:claim")[:16]
#   h2_hub = sha256(f"{h1_hub}|{','.join(sorted([h1_src, h1_src, h1_tgt]))}")[:16]
#   h2_src = sha256(f"{h1_src}|{h1_hub}")[:16]
#   h2_tgt = sha256(f"{h1_tgt}|{h1_hub}")[:16]
_EXPECTED_HUB_HASH = "8876cb32c32e53f4"
_EXPECTED_SOURCE_HASH = "c4ea463bfa8c78f8"
_EXPECTED_TARGET_HASH = "4547db8325b1cde6"


def _seed_isolated_concepts(multi: MultiGraph, count: int) -> None:
    store = multi.get_store()
    for i in range(count):
        store.create_entity(
            EntityCreate.model_validate(
                {"name": f"C{i}", "entityType": "concept", "observations": []}
            )
        )


def test_reify_patterns_golden_fingerprint_depth1(multi: MultiGraph) -> None:
    _seed_isolated_concepts(multi, 3)
    result = reify_patterns(
        ReifyPatternsInput.model_validate({"maxDepth": 1, "minOccurrences": 3}), multi
    )
    assert result["patternsDetected"] == 1
    pattern = result["patterns"][0]
    assert pattern["fingerprint"] == _EXPECTED_DEPTH1_HASH
    assert pattern["memberCount"] == 3
    assert pattern["description"] == "isolated concept"


def test_reify_patterns_golden_fingerprint_depth2_default(multi: MultiGraph) -> None:
    _seed_isolated_concepts(multi, 3)
    result = reify_patterns(ReifyPatternsInput.model_validate({"minOccurrences": 3}), multi)
    assert result["patternsDetected"] == 1
    pattern = result["patterns"][0]
    assert pattern["fingerprint"] == _EXPECTED_DEPTH2_HASH
    assert pattern["memberCount"] == 3


def _seed_hubs(multi: MultiGraph, count: int) -> None:
    """``count`` identical hub structures: two evidence nodes each supporting
    a claim, which references a concept."""
    store = multi.get_store()

    def entity(name: str, entity_type: str) -> str:
        return store.create_entity(
            EntityCreate.model_validate(
                {"name": name, "entityType": entity_type, "observations": []}
            )
        ).id

    def relate(from_id: str, to_id: str, relation_type: str) -> None:
        store.create_relation(
            RelationCreate.model_validate(
                {
                    "from": from_id,
                    "to": to_id,
                    "relationType": relation_type,
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": None,
                }
            )
        )

    for i in range(count):
        hub = entity(f"H{i}", "claim")
        target = entity(f"T{i}", "concept")
        relate(entity(f"S{i}a", "evidence"), hub, "supports")
        relate(entity(f"S{i}b", "evidence"), hub, "supports")
        relate(hub, target, "references")


def test_reify_patterns_golden_fingerprint_with_edges(multi: MultiGraph) -> None:
    """The edgeful golden: pins neighbor iteration, per-edge duplicate
    keeping, dedup-by-node-id, the depth-2 fold, and describe-with-edges —
    the surfaces an implementation change could actually alter."""
    _seed_hubs(multi, 3)
    result = reify_patterns(ReifyPatternsInput.model_validate({"minOccurrences": 3}), multi)
    assert result["patternsDetected"] == 3

    by_fingerprint = {p["fingerprint"]: p for p in result["patterns"]}
    assert by_fingerprint.keys() == {
        _EXPECTED_HUB_HASH,
        _EXPECTED_SOURCE_HASH,
        _EXPECTED_TARGET_HASH,
    }
    assert by_fingerprint[_EXPECTED_SOURCE_HASH]["memberCount"] == 6
    assert by_fingerprint[_EXPECTED_HUB_HASH]["memberCount"] == 3
    assert by_fingerprint[_EXPECTED_TARGET_HASH]["memberCount"] == 3
    # Groups sort by (count desc, fingerprint asc): sources first.
    assert result["patterns"][0]["fingerprint"] == _EXPECTED_SOURCE_HASH
    assert by_fingerprint[_EXPECTED_HUB_HASH]["description"] == (
        "claim with incoming [supports, supports] and outgoing [references] "
        "connected to [concept, evidence, evidence]"
    )
