"""Cross-file edge resolution.

The per-file tree-sitter pass leaves two joins dangling: an import points at
the raw module string the source wrote, and a call to an imported symbol has no
target. Before resolution, every import edge referenced a name that was never
an entity, so ``bulk_import`` silently dropped all of them and the graph came
out as one disconnected component per directory. These tests pin the join.
"""

from __future__ import annotations

import pytest

from theloom.extraction import resolution, treesitter

FILES = frozenset(
    {
        "src/models.py",
        "src/service.py",
        "pkg/__init__.py",
        "pkg/deep/mod.py",
        "lib/helper.js",
        "lib/index.ts",
        "lib/nested/widget.tsx",
        "ui/index.ts",
    }
)


class TestResolveModule:
    @pytest.mark.parametrize(
        ("module", "importer", "expected"),
        [
            # Python dotted paths, including the package __init__ form
            ("src.models", "src/service.py", "src/models.py"),
            ("pkg", "src/service.py", "pkg/__init__.py"),
            ("pkg.deep.mod", "src/service.py", "pkg/deep/mod.py"),
            # Python relative imports: one dot is the importer's own package
            (".models", "src/service.py", "src/models.py"),
            ("..pkg.deep.mod", "pkg/deep/mod.py", None),
            # JS/TS relative specifiers resolve across extensions...
            ("./helper", "lib/index.ts", "lib/helper.js"),
            ("../helper", "lib/nested/widget.tsx", "lib/helper.js"),
            ("./nested/widget", "lib/index.ts", "lib/nested/widget.tsx"),
            # ...and via a directory's index file
            ("../ui", "lib/index.ts", "ui/index.ts"),
            # Third-party packages resolve to nothing — they are not files here
            ("react", "lib/index.ts", None),
            ("os.path", "src/service.py", None),
            ("", "src/service.py", None),
        ],
    )
    def test_resolution(self, module: str, importer: str, expected: str | None) -> None:
        assert resolution.resolve_module(module, importer, FILES) == expected

    def test_escaping_the_project_root_resolves_to_nothing(self) -> None:
        assert resolution.resolve_module("../../outside", "lib/index.ts", FILES) is None


class TestExternalPackage:
    @pytest.mark.parametrize(
        ("module", "package"),
        [
            ("react", "react"),
            ("theloom.cli.registry", "theloom"),
            ("sigma/rendering", "sigma"),
            ("@scope/pkg", "@scope/pkg"),
            ("@scope/pkg/sub", "@scope/pkg"),
        ],
    )
    def test_top_level_package(self, module: str, package: str) -> None:
        assert resolution.external_package(module) == package


class TestResolveImports:
    def test_internal_import_becomes_a_file_to_file_edge(self) -> None:
        per_file = [
            {"path": "src/service.py", "imports": [{"module": "src.models", "names": ["Account"]}]},
            {"path": "src/models.py", "imports": []},
        ]
        out = resolution.resolve_imports(per_file, FILES)
        assert [(r["from"], r["to"]) for r in out["relations"]] == [
            ("file:src/service.py", "file:src/models.py")
        ]
        # The source states the import outright, so the edge is observed, not deduced.
        assert out["relations"][0]["confidence"]["basis"] == "direct_observation"
        assert out["stats"]["internalImports"] == 1
        assert out["entities"] == []

    def test_external_import_creates_one_package_entity(self) -> None:
        per_file = [
            {"path": "lib/index.ts", "imports": [{"module": "react", "names": ["useState"]}]},
            {"path": "ui/index.ts", "imports": [{"module": "react/jsx-runtime", "names": []}]},
        ]
        out = resolution.resolve_imports(per_file, FILES)
        assert [e["name"] for e in out["entities"]] == ["pkg:react"]
        assert out["entities"][0]["entityType"] == "system"
        assert {r["to"] for r in out["relations"]} == {"pkg:react"}
        assert out["stats"]["externalImports"] == 2

    def test_external_entities_can_be_suppressed(self) -> None:
        per_file = [{"path": "lib/index.ts", "imports": [{"module": "react", "names": []}]}]
        out = resolution.resolve_imports(per_file, FILES, external_entities=False)
        assert out["entities"] == []
        assert out["relations"] == []
        assert out["stats"]["unresolvedImports"] == 1

    def test_a_file_importing_itself_is_not_an_edge(self) -> None:
        per_file = [{"path": "src/models.py", "imports": [{"module": "src.models", "names": []}]}]
        assert resolution.resolve_imports(per_file, FILES)["relations"] == []

    def test_repeated_imports_collapse_to_one_edge(self) -> None:
        per_file = [
            {
                "path": "src/service.py",
                "imports": [
                    {"module": "src.models", "names": ["Account"]},
                    {"module": "src.models", "names": ["open_account"]},
                ],
            }
        ]
        assert len(resolution.resolve_imports(per_file, FILES)["relations"]) == 1


class TestResolveCalls:
    def test_import_evidence_proves_the_target(self) -> None:
        per_file = [
            {
                "path": "src/service.py",
                "imports": [{"module": "src.models", "names": ["open_account"]}],
                "symbols": {"onboard": "onboard (service)"},
                "unresolvedCalls": [
                    {"caller": "onboard (service)", "callee": "open_account", "line": 11}
                ],
            },
            {
                "path": "src/models.py",
                "imports": [],
                "symbols": {"open_account": "open_account (models)"},
                "unresolvedCalls": [],
            },
        ]
        out = resolution.resolve_symbol_edges(
            per_file,
            FILES,
            field="unresolvedCalls",
            relation_type="calls",
            verb="calls",
            anchored=True,
        )
        assert [(r["from"], r["to"], r["relationType"]) for r in out["relations"]] == [
            ("onboard (service)", "open_account (models)", "calls")
        ]
        # The anchor is the call site in the caller's file, not the callee's
        # definition; the format is fixed so a reader can parse it.
        assert (
            out["relations"][0]["evidence"]
            == "onboard (service) calls open_account at src/service.py:12"
        )
        assert out["relations"][0]["polarity"] is None
        assert out["relations"][0]["confidence"]["basis"] == "direct_observation"
        assert out["stats"]["proven"] == 1

    def test_a_project_unique_name_resolves_as_a_deduction(self) -> None:
        per_file = [
            {
                "path": "src/service.py",
                "language": "python",
                "imports": [],
                "symbols": {"onboard": "onboard (service)"},
                "symbolKinds": {"onboard": "procedure"},
                "unresolvedCalls": [
                    {"caller": "onboard (service)", "callee": "only_one", "line": 41}
                ],
            },
            {
                "path": "src/models.py",
                "language": "python",
                "imports": [],
                "symbols": {"only_one": "only_one (models)"},
                "symbolKinds": {"only_one": "procedure"},
                "unresolvedCalls": [],
            },
        ]
        out = resolution.resolve_symbol_edges(
            per_file,
            FILES,
            field="unresolvedCalls",
            relation_type="calls",
            verb="calls",
            anchored=True,
        )
        assert out["relations"][0]["to"] == "only_one (models)"
        assert out["relations"][0]["relationType"] == "calls"
        # A deduced edge is anchored the same way; only the confidence basis
        # records that no import proved it.
        assert (
            out["relations"][0]["evidence"]
            == "onboard (service) calls only_one at src/service.py:42"
        )
        # No import names it, so the link is deduced rather than observed.
        assert out["relations"][0]["confidence"]["basis"] == "inference"
        assert out["stats"]["inferred"] == 1

    def test_an_ambiguous_name_resolves_to_nothing(self) -> None:
        """A wrong edge is worse than a missing one: cycles, centrality and
        component analysis all read edges as fact."""
        per_file = [
            {
                "path": "src/service.py",
                "language": "python",
                "imports": [],
                "symbols": {"onboard": "onboard (service)"},
                "symbolKinds": {"onboard": "procedure"},
                "unresolvedCalls": [{"caller": "onboard (service)", "callee": "run"}],
            },
            {
                "path": "src/models.py",
                "language": "python",
                "imports": [],
                "symbols": {"run": "run (models)"},
                "symbolKinds": {"run": "procedure"},
            },
            {
                "path": "src/other.py",
                "language": "python",
                "imports": [],
                "symbols": {"run": "run (other)"},
                "symbolKinds": {"run": "procedure"},
            },
        ]
        out = resolution.resolve_symbol_edges(
            per_file,
            FILES,
            field="unresolvedCalls",
            relation_type="calls",
            verb="calls",
            anchored=True,
        )
        assert out["relations"] == []
        assert out["stats"]["ambiguous"] == 1

    def test_import_evidence_beats_an_ambiguous_name(self) -> None:
        per_file = [
            {
                "path": "src/service.py",
                "imports": [{"module": "src.models", "names": ["run"]}],
                "symbols": {"onboard": "onboard (service)"},
                "unresolvedCalls": [{"caller": "onboard (service)", "callee": "run"}],
            },
            {"path": "src/models.py", "imports": [], "symbols": {"run": "run (models)"}},
            {"path": "lib/helper.js", "imports": [], "symbols": {"run": "run (helper)"}},
        ]
        out = resolution.resolve_symbol_edges(
            per_file,
            FILES,
            field="unresolvedCalls",
            relation_type="calls",
            verb="calls",
            anchored=True,
        )
        assert [(r["from"], r["to"], r["relationType"]) for r in out["relations"]] == [
            ("onboard (service)", "run (models)", "calls")
        ]


class TestIsTestPath:
    """One answer to "is this the product?", shared by the file collector and
    the doc linker's vocabulary — a repo whose tests are ``tests/test_*.py``
    must not read as product source in one pass and as tests in another."""

    @pytest.mark.parametrize(
        "path",
        [
            "tests/test_work_memory.py",
            "tests/conftest.py",
            "src/store_test.py",
            "tapestry/src/lib/schema.test.ts",
            "tapestry/src/views/smoke.spec.ts",
            "app/__tests__/render.tsx",
            "TESTS/Test_Thing.PY",
        ],
    )
    def test_test_files_are_recognised(self, path: str) -> None:
        assert resolution.is_test_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            "theloom/store/falkor.py",
            "theloom/extraction/latest.py",
            "docs/superpowers/specs/2026-07-11-design.md",
            "src/contest.py",
        ],
    )
    def test_product_source_is_not(self, path: str) -> None:
        assert not resolution.is_test_path(path)


class TestUniqueNameGuards:
    """The unique-name rule is the low-precision resolver, so it needs guards.

    Without them a single project symbol that happens to share a common name
    absorbs every unqualified caller: 288 Python ``len()`` calls resolved to a
    lone TypeScript ``len`` constant, making it the most-connected node in the
    graph and welding the frontend to the backend. Typing these edges ``calls``
    makes them easier to find, so the guards matter more, not less.
    """

    def _calls(self, callee: str, target_lang: str, target_kind: str) -> dict[str, object]:
        return resolution.resolve_symbol_edges(
            [
                {
                    "path": "src/service.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {"onboard": "onboard (service)"},
                    "symbolKinds": {"onboard": "procedure"},
                    "unresolvedCalls": [
                        {"caller": "onboard (service)", "callee": callee, "line": 6}
                    ],
                },
                {
                    "path": "lib/index.ts",
                    "language": target_lang,
                    "imports": [],
                    "symbols": {callee: f"{callee} (index)"},
                    "symbolKinds": {callee: target_kind},
                    "unresolvedCalls": [],
                },
            ],
            FILES,
            field="unresolvedCalls",
            relation_type="calls",
            verb="calls",
            anchored=True,
        )

    def test_a_language_builtin_never_resolves(self) -> None:
        """No ``calls`` edge either — the guard drops the edge, not its type."""
        assert self._calls("len", "python", "procedure")["relations"] == []

    def test_a_call_does_not_cross_a_language_boundary(self) -> None:
        """A Python file cannot call a TypeScript symbol."""
        assert self._calls("helper", "typescript", "procedure")["relations"] == []

    def test_a_non_callable_symbol_is_not_a_call_target(self) -> None:
        """A local constant that merely shares the name is not a dependency."""
        assert self._calls("helper", "python", "variable")["relations"] == []

    def test_a_same_language_callable_still_resolves(self) -> None:
        out = self._calls("helper", "python", "procedure")
        assert [(r["from"], r["to"], r["relationType"]) for r in out["relations"]] == [
            ("onboard (service)", "helper (index)", "calls")
        ]
        assert out["relations"][0]["evidence"] == (
            "onboard (service) calls helper at src/service.py:7"
        )
        assert out["relations"][0]["confidence"]["basis"] == "inference"


class TestResolveInheritances:
    def test_imported_base_class_resolves_to_its_real_file(self) -> None:
        """Naming an imported base as though it lived in the subclass's file
        invented an entity that was never created, so the edge was dropped."""
        per_file = [
            {
                "path": "src/service.py",
                "imports": [{"module": "src.models", "names": ["Account"]}],
                "symbols": {"Savings": "Savings (service)"},
                "unresolvedInheritances": [{"caller": "Savings (service)", "callee": "Account"}],
            },
            {
                "path": "src/models.py",
                "imports": [],
                "symbols": {"Account": "Account (models)"},
                "unresolvedInheritances": [],
            },
        ]
        out = resolution.resolve_symbol_edges(
            per_file,
            FILES,
            field="unresolvedInheritances",
            relation_type="instance_of",
            verb="extends",
        )
        assert [(r["from"], r["to"], r["relationType"]) for r in out["relations"]] == [
            ("Savings (service)", "Account (models)", "instance_of")
        ]
        # Inheritance evidence is untouched by the call-site anchoring: a base
        # class is named at the class header, not at a call site.
        assert (
            out["relations"][0]["evidence"]
            == "Savings (service) extends Account, imported from src/models.py"
        )
        assert out["stats"]["proven"] == 1

    def test_an_ambiguous_base_class_resolves_to_nothing(self) -> None:
        per_file = [
            {
                "path": "src/service.py",
                "language": "python",
                "imports": [],
                "symbols": {"Savings": "Savings (service)"},
                "symbolKinds": {"Savings": "concept"},
                "unresolvedInheritances": [{"caller": "Savings (service)", "callee": "Base"}],
            },
            {
                "path": "src/models.py",
                "language": "python",
                "imports": [],
                "symbols": {"Base": "Base (models)"},
                "symbolKinds": {"Base": "concept"},
            },
            {
                "path": "src/other.py",
                "language": "python",
                "imports": [],
                "symbols": {"Base": "Base (other)"},
                "symbolKinds": {"Base": "concept"},
            },
        ]
        out = resolution.resolve_symbol_edges(
            per_file,
            FILES,
            field="unresolvedInheritances",
            relation_type="instance_of",
            verb="extends",
        )
        assert out["relations"] == []
        assert out["stats"]["ambiguous"] == 1


class TestEndToEnd:
    """The fixture repo exercises both resolvers in both language families."""

    def test_imports_and_calls_span_files(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        edges = {(r["from"], r["to"], r["relationType"]) for r in result["relations"]}

        # Python: a dotted import and the call it enables
        assert ("file:src/service.py", "file:src/models.py", "requires") in edges
        assert ("onboard (service)", "open_account (models)", "calls") in edges
        # TS -> JS: an extensionless specifier resolved across extensions, and a
        # call made from inside a method body
        assert ("file:lib/index.ts", "file:lib/helper.js", "requires") in edges
        assert ("Reporter.summarize (index)", "formatBalance (helper)", "calls") in edges
        # Third-party imports become one package node
        assert ("file:src/models.py", "pkg:dataclasses", "requires") in edges

    def test_structural_extraction_never_emits_related_to(self) -> None:
        """``related_to`` is the semantic layer's grounding link. Structural
        extraction used to spend it on call edges, which made the two
        indistinguishable once both were in one graph."""
        result = treesitter.extract_codebase("tests/fixtures/repo")
        assert {r["relationType"] for r in result["relations"]} == {
            "part_of",
            "requires",
            "calls",
            # A doc naming a file or a symbol; still structural, still not a
            # semantic grounding link.
            "references",
        }

    def test_every_call_edge_is_anchored_at_its_call_site(self) -> None:
        """One machine-stable format, whichever resolver produced the edge."""
        result = treesitter.extract_codebase("tests/fixtures/repo")
        evidence = sorted(
            r["evidence"] for r in result["relations"] if r["relationType"] == "calls"
        )
        assert evidence == [
            # cross-file, TS -> JS, resolved through the import
            "Reporter.summarize (index) calls formatBalance at lib/index.ts:12",
            # cross-file, resolved through the import
            "onboard (service) calls open_account at src/service.py:12",
            # same file: the site is where the call is written, not line 7
            # where Account is defined
            "open_account (models) calls Account at src/models.py:21",
        ]

    def test_no_edge_points_at_a_nonexistent_entity(self) -> None:
        """The bug this module exists to fix: import edges named a raw module
        string that was never created, so every one was dropped on import.

        ``bulk_import`` drops an unresolvable relation with a per-item error
        rather than failing the run, so a whole class of edge can vanish
        unnoticed — 1,270 of them did. Every relation type gets checked, and
        each type must actually be present, so a type that stops being emitted
        cannot pass this test by having nothing to check.
        """
        result = treesitter.extract_codebase("tests/fixtures/repo")
        names = {e["name"] for e in result["entities"]}
        dangling = [
            (r["from"], r["to"], r["relationType"])
            for r in result["relations"]
            if r["from"] not in names or r["to"] not in names
        ]
        assert dangling == []
        assert {r["relationType"] for r in result["relations"]} >= {"calls", "requires", "part_of"}

    def test_generated_entities_satisfy_the_domain_model(self) -> None:
        """Extraction output crosses into the store through EntityCreate and
        RelationCreate, so a field the model rejects fails the whole import — a
        unit test that only checks dict shape will not catch it. Relations go
        through too, because an unregistered relationType (``calls`` before it
        joined the enum) is exactly that kind of failure."""
        from theloom.model import EntityCreate, RelationCreate

        result = treesitter.extract_codebase("tests/fixtures/repo")
        for entity in result["entities"]:
            EntityCreate.model_validate(entity)
        for relation in result["relations"]:
            RelationCreate.model_validate(relation)

    def test_resolution_stats_are_reported(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        stats = result["resolution"]
        assert stats["internalImports"] == 2
        assert stats["externalImports"] == 1
        assert stats["importGuidedCalls"] == 2
        assert stats["ambiguousCallsSkipped"] == 0
