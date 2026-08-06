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

from theloom.model import EntityCreate
from theloom.operations.reification import ReifyPatternsInput, reify_patterns
from theloom.store.multigraph import MultiGraph

# Hand-computed via hashlib.sha256(...).hexdigest()[:16]:
#   depth1 = sha256("concept|in:|out:|neighbors:")[:16]
#   depth2 = sha256(f"{depth1}|")[:16]
_EXPECTED_DEPTH1_HASH = "1520bf3922b142b8"
_EXPECTED_DEPTH2_HASH = "39c203a283d6a407"


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
