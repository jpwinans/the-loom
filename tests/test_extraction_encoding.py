"""theloom.extraction.encoding: the single place the codebase-graph string
formats are built and parsed.

Two kinds of test:

* **round-trip** — ``build(x)`` then ``parse(...)`` recovers ``x``, over a
  handful of independently chosen example inputs (not derived from the code
  under test);
* **literal** — parsing a string copied verbatim from the current writer
  formats (as pinned by tests/test_extraction_units.py,
  tests/test_extraction_resolution.py, and the evidence in the format's own
  docstrings), so any drift between the module and what is actually stored in
  existing graphs fails loudly.
"""

from __future__ import annotations

from theloom.extraction import encoding


class TestFileEntityName:
    def test_round_trip(self) -> None:
        for path in ["theloom/model.py", "src/a/b/c.ts", "README.md"]:
            assert encoding.parse_file_entity_name(encoding.file_entity_name(path)) == path

    def test_literal(self) -> None:
        # Copied from tests/test_extraction_units.py::test_symbol_part_of_file_and_enclosing.
        assert encoding.parse_file_entity_name("file:c.py") == "c.py"

    def test_non_file_entity_name_parses_to_none(self) -> None:
        assert encoding.parse_file_entity_name("pkg:requests") is None
        assert encoding.parse_file_entity_name("caller (mod)") is None

    def test_is_file_entity_name(self) -> None:
        assert encoding.is_file_entity_name("file:theloom/model.py") is True
        assert encoding.is_file_entity_name("theloom.model.LoomModel (model)") is False


class TestFilePathObservation:
    def test_round_trip(self) -> None:
        for path in ["theloom/model.py", "src/a/b/c.ts"]:
            built = encoding.file_path_observation(path)
            assert encoding.parse_file_path([built]) == path

    def test_literal(self) -> None:
        # Copied from tests/test_extraction_units.py::test_text_files_become_root_file_entities.
        observations = ["File path: styles/tokens.css", "Language: css", "Symbol kind: File"]
        assert encoding.parse_file_path(observations) == "styles/tokens.css"

    def test_case_insensitive_like_the_readers_it_replaces(self) -> None:
        # operations/common.py and composites/reflect.py historically matched
        # the prefix lowercase; parsing must keep tolerating that.
        assert encoding.parse_file_path(["file path: theloom/x.py"]) == "theloom/x.py"

    def test_absent_prefix_parses_to_none(self) -> None:
        assert encoding.parse_file_path(["Language: python", "docstring: hi"]) is None
        assert encoding.parse_file_path([]) is None


class TestLineRangeObservation:
    def test_round_trip(self) -> None:
        for start, end in [(0, 5), (41, 41), (99, 200)]:
            built = encoding.line_range_observation(start, end)
            assert encoding.parse_line_range([built]) == (start, end)

    def test_literal(self) -> None:
        # Copied from tests/test_extraction_units.py::
        # test_python_function_carries_signature_and_docstring — 0-based lines
        # 0-5 render as "1-6".
        assert encoding.parse_line_range(["Line range: 1-6"]) == (0, 5)

    def test_case_insensitive_like_the_readers_it_replaces(self) -> None:
        assert encoding.parse_line_range(["line range: 30-60"]) == (29, 59)

    def test_absent_prefix_parses_to_none(self) -> None:
        assert encoding.parse_line_range(["File path: x.py"]) is None


class TestSymbolKindObservation:
    def test_round_trip(self) -> None:
        for kind in ["function", "class", "File", "ExternalPackage"]:
            built = encoding.symbol_kind_observation(kind)
            assert encoding.parse_symbol_kind([built]) == kind

    def test_literal(self) -> None:
        # Copied from tests/test_extraction_units.py::
        # test_python_function_carries_signature_and_docstring.
        assert encoding.parse_symbol_kind(["Symbol kind: function"]) == "function"

    def test_case_insensitive_like_the_readers_it_replaces(self) -> None:
        assert encoding.parse_symbol_kind(["symbol kind: Class"]) == "Class"

    def test_absent_prefix_parses_to_none(self) -> None:
        assert encoding.parse_symbol_kind(["File path: x.py"]) is None


class TestCallEvidence:
    def test_round_trip(self) -> None:
        for caller, callee, path, line in [
            ("caller (mod)", "helper (mod)", "src/mod.py", 5),
            ("run", "helper", "theloom/a.py", 14),
        ]:
            evidence = encoding.call_evidence(caller, callee, path, line)
            assert encoding.parse_call_evidence(evidence) == (caller, callee, path, line)

    def test_literal(self) -> None:
        # Copied from tests/test_extraction_units.py::
        # test_a_same_file_call_is_anchored_at_its_call_site — 0-based line 5
        # renders as ":6". The evidence carries the *bare* callee name (as the
        # writer received it), not the qualified entity name in "to".
        evidence = "caller (mod) calls helper at src/mod.py:6"
        assert encoding.parse_call_evidence(evidence) == ("caller (mod)", "helper", "src/mod.py", 5)

    def test_call_site_text_literal(self) -> None:
        # Copied from tests/test_consumption.py's fixture evidence strings —
        # the raw "<file>:<1-based line>" substring readers display verbatim.
        evidence = "caller_one calls run at theloom/b.py:12"
        assert encoding.parse_call_site_text(evidence) == "theloom/b.py:12"

    def test_call_site_text_absent_parses_to_none(self) -> None:
        assert encoding.parse_call_site_text(None) is None
        assert encoding.parse_call_site_text("not anchored at anything") is None
