"""Documentation-to-code linking.

Markdown files already became file entities, but with no edges at all: the
docs formed islands, and drift between what a doc specifies and what the code
does was invisible to every graph query. This pass joins them, deterministically
and without an LLM — a repo-relative path that exists in the extraction, or a
backtick-quoted symbol name that is the project's *only* symbol of that name.

The guard is the lesson of the resolver's false positives: a bare word in prose
is never a link, an ambiguous name is never a link, and a language builtin is
never a link. A wrong edge is worse than a missing one.
"""

from __future__ import annotations

from typing import Any

from theloom.extraction import doclinks, treesitter

Doc = dict[str, Any]

FILE_PATHS = frozenset(
    {
        "src/models.py",
        "src/service.py",
        "lib/helper.js",
        "lib/index.ts",
        "docs/architecture.md",
    }
)

PER_FILE: list[Doc] = [
    {
        "path": "src/models.py",
        "symbols": {"Account": "Account (models)", "open_account": "open_account (models)"},
        "symbolKinds": {"Account": "concept", "open_account": "procedure"},
    },
    {
        "path": "lib/helper.js",
        "symbols": {"roundCents": "roundCents (helper)"},
        "symbolKinds": {"roundCents": "procedure"},
    },
    {
        "path": "lib/index.ts",
        # A name defined in two files: mentioning it names no single symbol.
        "symbols": {"roundCents": "roundCents (index)", "Reporter": "Reporter (index)"},
        "symbolKinds": {"roundCents": "procedure", "Reporter": "concept"},
    },
]


def _link(content: str, path: str = "docs/architecture.md", **kwargs: Any) -> Doc:
    return doclinks.resolve_doc_links(
        [{"path": path, "content": content}], FILE_PATHS, PER_FILE, **kwargs
    )


class TestPathMentions:
    def test_an_existing_repo_relative_path_becomes_a_references_edge(self) -> None:
        out = _link("The models live in src/models.py today.\n")
        assert [(r["from"], r["to"], r["relationType"]) for r in out["relations"]] == [
            ("file:docs/architecture.md", "file:src/models.py", "references")
        ]
        relation = out["relations"][0]
        assert relation["evidence"] == "mentions src/models.py at docs/architecture.md:1"
        assert relation["polarity"] is None
        # The doc writes the path outright, so the link is observed, not deduced.
        assert relation["confidence"]["basis"] == "direct_observation"
        assert out["stats"]["docPathReferences"] == 1

    def test_a_path_that_is_not_in_the_extraction_links_to_nothing(self) -> None:
        assert _link("See src/missing.py for the plan.\n")["relations"] == []

    def test_a_doc_does_not_reference_itself(self) -> None:
        assert _link("This file is docs/architecture.md.\n")["relations"] == []

    def test_the_line_number_is_the_mention_site(self) -> None:
        out = _link("# Title\n\nThe entry point is lib/index.ts.\n")
        assert out["relations"][0]["evidence"].endswith("docs/architecture.md:3")


class TestSymbolMentions:
    def test_a_backticked_unique_symbol_becomes_a_references_edge(self) -> None:
        out = _link("Call `open_account` to make one.\n")
        assert [(r["from"], r["to"]) for r in out["relations"]] == [
            ("file:docs/architecture.md", "open_account (models)")
        ]
        relation = out["relations"][0]
        assert relation["evidence"] == "mentions open_account at docs/architecture.md:1"
        # Nothing states the link; a unique name match is a deduction.
        assert relation["confidence"]["basis"] == "inference"
        assert out["stats"]["docSymbolReferences"] == 1

    def test_a_call_form_is_the_same_mention(self) -> None:
        out = _link("Call `open_account()` to make one.\n")
        assert [r["to"] for r in out["relations"]] == ["open_account (models)"]

    def test_an_ambiguous_name_links_to_nothing(self) -> None:
        """Two files define ``roundCents``; picking one would be a guess
        presented as structure — the defect PR #7 removed from the resolver."""
        out = _link("Rounding is done by `roundCents`.\n")
        assert out["relations"] == []
        assert out["stats"]["ambiguousDocMentionsSkipped"] == 1

    def test_a_bare_word_in_prose_is_never_a_link(self) -> None:
        assert _link("An Account holds a balance; open_account makes one.\n")["relations"] == []

    def test_a_language_builtin_is_never_a_link(self) -> None:
        per_file = [
            *PER_FILE,
            {
                "path": "lib/helper.js",
                "symbols": {"len": "len (helper)"},
                "symbolKinds": {"len": "procedure"},
            },
        ]
        out = doclinks.resolve_doc_links(
            [{"path": "docs/architecture.md", "content": "Use `len` for sizes.\n"}],
            FILE_PATHS,
            per_file,
        )
        assert out["relations"] == []

    def test_a_lone_lowercase_word_in_backticks_is_never_a_link(self) -> None:
        """Backticks alone do not make a word code.

        ``Keep `main` green`` means the git branch, and the project's only
        ``main`` is the CLI entry point — the unique-name rule would weld the
        doc to it. A name links only when it is *written* the way code is:
        qualified, snake_case, camel/PascalCase, or an explicit call form.
        """
        per_file = [
            {
                "path": "src/cli.py",
                "symbols": {"main": "main (cli)", "run": "run (cli)"},
                "symbolKinds": {"main": "procedure", "run": "procedure"},
            }
        ]
        out = doclinks.resolve_doc_links(
            [
                {
                    "path": "docs/architecture.md",
                    "content": "Keep `main` green; `run` the suite first.\n",
                }
            ],
            FILE_PATHS,
            per_file,
        )
        assert out["relations"] == []
        assert out["stats"]["docSymbolReferences"] == 0

    def test_the_call_form_rescues_a_lowercase_word(self) -> None:
        per_file = [
            {
                "path": "src/cli.py",
                "symbols": {"main": "main (cli)"},
                "symbolKinds": {"main": "procedure"},
            }
        ]
        out = doclinks.resolve_doc_links(
            [{"path": "docs/architecture.md", "content": "Entry point: `main()`.\n"}],
            FILE_PATHS,
            per_file,
        )
        assert [r["to"] for r in out["relations"]] == ["main (cli)"]

    def test_a_non_callable_symbol_is_never_a_link(self) -> None:
        """A backticked config key or JSON field is not a reference to the
        module-level variable that happens to share its name — the resolver's
        callable-kind guard, applied here."""
        per_file = [
            {
                "path": "src/settings.py",
                "symbols": {"graph_name": "graph_name (settings)"},
                "symbolKinds": {"graph_name": "variable"},
            }
        ]
        out = doclinks.resolve_doc_links(
            [{"path": "docs/architecture.md", "content": "Set `graph_name` in the config.\n"}],
            FILE_PATHS,
            per_file,
        )
        assert out["relations"] == []

    def test_a_very_short_name_is_never_a_link(self) -> None:
        per_file = [
            {
                "path": "src/models.py",
                "symbols": {"ok": "ok (models)"},
                "symbolKinds": {"ok": "procedure"},
            }
        ]
        out = doclinks.resolve_doc_links(
            [{"path": "docs/architecture.md", "content": "Return `ok` on success.\n"}],
            FILE_PATHS,
            per_file,
        )
        assert out["relations"] == []

    def test_backticked_prose_is_not_an_identifier(self) -> None:
        assert _link("Run `open_account and pray`.\n")["relations"] == []

    def test_a_qualified_method_name_resolves(self) -> None:
        summarize = "Reporter.summarize (index)"
        per_file = [
            {
                "path": "lib/index.ts",
                "symbols": {"Reporter.summarize": summarize},
                "symbolKinds": {"Reporter.summarize": "procedure"},
            }
        ]
        out = doclinks.resolve_doc_links(
            [{"path": "docs/architecture.md", "content": "See `Reporter.summarize`.\n"}],
            FILE_PATHS,
            per_file,
        )
        assert [r["to"] for r in out["relations"]] == [summarize]


class TestDedupeAndCap:
    def test_repeated_mentions_collapse_to_one_edge(self) -> None:
        out = _link("src/models.py is here.\nAnd `open_account` is in src/models.py.\n")
        assert [r["to"] for r in out["relations"]] == [
            "file:src/models.py",
            "open_account (models)",
        ]

    def test_out_degree_is_capped_and_the_drop_is_counted(self) -> None:
        """One doc listing every file in the repo would otherwise become the
        most-connected node in the graph without saying anything."""
        content = "src/models.py src/service.py lib/helper.js lib/index.ts\n"
        out = _link(content, max_links=2)
        assert len(out["relations"]) == 2
        assert out["stats"]["docReferencesCapped"] == 2

    def test_the_cap_is_per_document(self) -> None:
        out = doclinks.resolve_doc_links(
            [
                {"path": "docs/architecture.md", "content": "src/models.py src/service.py\n"},
                {"path": "docs/other.md", "content": "src/models.py src/service.py\n"},
            ],
            FILE_PATHS,
            PER_FILE,
            max_links=2,
        )
        assert len(out["relations"]) == 4
        assert out["stats"]["docReferencesCapped"] == 0


class TestFixtureRepo:
    """The fixture repo carries one doc that links and one that must not."""

    def test_the_linking_doc_reaches_files_and_symbols(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        edges = {(r["from"], r["to"], r["relationType"]) for r in result["relations"]}
        assert ("file:docs/architecture.md", "file:src/models.py", "references") in edges
        assert ("file:docs/architecture.md", "file:src/service.py", "references") in edges
        assert ("file:docs/architecture.md", "open_account (models)", "references") in edges
        assert ("file:docs/architecture.md", "allows (policy)", "references") in edges

    def test_the_ambiguous_doc_links_to_nothing(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        assert [r for r in result["relations"] if r["from"] == "file:docs/glossary.md"] == []
        assert result["resolution"]["ambiguousDocMentionsSkipped"] == 1

    def test_prose_mentions_in_the_linking_doc_are_not_edges(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        targets = {r["to"] for r in result["relations"] if r["from"] == "file:docs/architecture.md"}
        # `Account` is named in prose and `src/missing.py` does not exist.
        assert "Account (models)" not in targets
        assert "file:src/missing.py" not in targets

    def test_no_references_edge_points_at_a_nonexistent_entity(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        names = {e["name"] for e in result["entities"]}
        references = [r for r in result["relations"] if r["relationType"] == "references"]
        assert references
        assert [r for r in references if r["from"] not in names or r["to"] not in names] == []

    def test_doc_link_stats_are_reported(self) -> None:
        stats = treesitter.extract_codebase("tests/fixtures/repo")["resolution"]
        assert stats["docPathReferences"] == 2
        assert stats["docSymbolReferences"] == 2
        assert stats["docReferencesCapped"] == 0
