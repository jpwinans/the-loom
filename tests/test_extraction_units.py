"""Unit tests for extraction internals — the pieces the fixed-repo golden
tests can't isolate (mapping determinism, language detection, no-LLM gating)."""

from __future__ import annotations

import pytest

from theloom.extraction import treesitter


class TestLanguageDetection:
    @pytest.mark.parametrize(
        ("path", "lang"),
        [
            ("a.py", "python"),
            ("a.ts", "typescript"),
            ("a.tsx", "typescript"),
            ("a.js", "javascript"),
            ("a.mjs", "javascript"),
            ("a.go", "go"),
            ("a.rs", "rust"),
            ("a.md", None),
            ("a.txt", None),
        ],
    )
    def test_detect(self, path: str, lang: str | None) -> None:
        assert treesitter.detect_language(path) == lang


class TestKindMapping:
    def test_entity_types(self) -> None:
        assert treesitter.kind_to_entity_type("class") == "concept"
        assert treesitter.kind_to_entity_type("interface") == "concept"
        assert treesitter.kind_to_entity_type("function") == "procedure"
        assert treesitter.kind_to_entity_type("method") == "procedure"
        assert treesitter.kind_to_entity_type("constant") == "variable"
        assert treesitter.kind_to_entity_type("variable") == "variable"


class TestExtractFromSource:
    def test_python_class_and_functions(self) -> None:
        source = (
            "class Account:\n"
            "    def deposit(self, amount):\n"
            "        return amount\n\n"
            "def open_account(name):\n"
            "    return Account()\n"
        )
        result = treesitter.extract_from_source(source, "src/models.py", "python")
        names = {e["name"]: e["entityType"] for e in result["entities"]}
        assert names["file:src/models.py"] == "system"
        assert names["Account (models)"] == "concept"
        assert names["Account.deposit (models)"] == "procedure"
        assert names["open_account (models)"] == "procedure"
        # open_account calls Account (both resolve) -> calls
        calls = [r for r in result["relations"] if r["relationType"] == "calls"]
        assert any(
            r["from"] == "open_account (models)" and r["to"] == "Account (models)" for r in calls
        )
        # related_to is reserved for the semantic layer; no code edge uses it.
        assert [r for r in result["relations"] if r["relationType"] == "related_to"] == []

    def test_a_same_file_call_is_anchored_at_its_call_site(self) -> None:
        """The evidence names the line the call is written on, not the line the
        callee is defined on — a reader follows the caller, not the target."""
        source = "def helper():\n    pass\n\n\ndef caller():\n    helper()\n"
        result = treesitter.extract_from_source(source, "src/mod.py", "python")
        calls = [r for r in result["relations"] if r["relationType"] == "calls"]
        assert [(r["from"], r["to"], r["evidence"]) for r in calls] == [
            (
                "caller (mod)",
                "helper (mod)",
                "caller (mod) calls helper at src/mod.py:6",
            )
        ]
        assert calls[0]["polarity"] is None

    def test_symbol_part_of_file_and_enclosing(self) -> None:
        source = "class C:\n    def m(self):\n        pass\n"
        result = treesitter.extract_from_source(source, "c.py", "python")
        part_of = [r for r in result["relations"] if r["relationType"] == "part_of"]
        pairs = {(r["from"], r["to"]) for r in part_of}
        assert ("C.m (c)", "file:c.py") in pairs
        assert ("C.m (c)", "C (c)") in pairs

    def test_typescript_interface_class_heritage(self) -> None:
        source = (
            "interface Ledger { entries: number[]; }\n"
            "class Reporter { summarize(): string { return ''; } }\n"
            "function make(): Reporter { return new Reporter(); }\n"
        )
        result = treesitter.extract_from_source(source, "lib/index.ts", "typescript")
        names = {e["name"]: e["entityType"] for e in result["entities"]}
        assert names["Ledger (index)"] == "concept"
        assert names["Reporter (index)"] == "concept"
        assert names["Reporter.summarize (index)"] == "procedure"
        assert names["make (index)"] == "procedure"

    def test_import_is_captured_with_its_bound_names(self) -> None:
        """The per-file pass records the import; it does not emit the edge.

        A single file cannot know which file ``dataclasses`` denotes, so the
        edge is made later against the full file set (see
        ``tests/test_extraction_resolution.py``). Emitting it here is what
        produced edges pointing at names that were never entities.
        """
        result = treesitter.extract_from_source(
            "from dataclasses import dataclass\n", "m.py", "python"
        )
        assert result["imports"] == [{"module": "dataclasses", "names": ["dataclass"]}]
        assert [r for r in result["relations"] if r["relationType"] == "requires"] == []

    def test_aliased_import_records_the_local_name(self) -> None:
        """Calling code writes the alias, so the alias is what call resolution
        must match on."""
        result = treesitter.extract_from_source(
            "from src.models import open_account as make\n", "m.py", "python"
        )
        assert result["imports"] == [{"module": "src.models", "names": ["make"]}]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            # ``import X as Y`` binds Y but imports X — capturing the whole
            # "X as Y" text produced package nodes literally named "numpy as np".
            ("import numpy as np\n", [{"module": "numpy", "names": ["np"]}]),
            ("import os\n", [{"module": "os", "names": []}]),
            ("import a.b.c\n", [{"module": "a.b.c", "names": []}]),
            ("from x import y as z\n", [{"module": "x", "names": ["z"]}]),
        ],
    )
    def test_import_forms(self, source: str, expected: list[dict[str, object]]) -> None:
        result = treesitter.extract_from_source(source, "m.py", "python")
        assert result["imports"] == expected

    def test_call_from_a_method_body_is_attributed_to_the_method(self) -> None:
        """The caller key must match how the symbol map names methods
        (``Class.method``); a bare name never matched, so every call made
        inside a method was discarded."""
        source = "class C:\n    def m(self):\n        helper()\n"
        result = treesitter.extract_from_source(source, "c.py", "python")
        # The call site travels with the call so the cross-file pass can anchor
        # its evidence at the line the call is written on.
        assert result["unresolvedCalls"] == [{"caller": "C.m (c)", "callee": "helper", "line": 2}]


def _observations(result: dict[str, object], name: str) -> list[str]:
    entities = result["entities"]
    assert isinstance(entities, list)
    for entity in entities:
        if entity["name"] == name:
            obs = entity["observations"]
            assert isinstance(obs, list)
            return obs
    raise AssertionError(f"no entity named {name!r}")


class TestSignaturesAndDocstrings:
    """A symbol used to carry only path/line/kind — three facts that say nothing
    about what it does, and give an embedding almost nothing to embed. The
    signature and the docstring are already in the parse tree."""

    def test_python_function_carries_signature_and_docstring(self) -> None:
        source = (
            "def open_account(name: str, balance: float = 0.0) -> Account:\n"
            '    """Create an empty account.\n\n    Extra prose.\n    """\n'
            "    return Account()\n"
        )
        result = treesitter.extract_from_source(source, "src/models.py", "python")
        obs = _observations(result, "open_account (models)")
        assert obs[:3] == [
            "File path: src/models.py",
            "Line range: 1-6",
            "Symbol kind: function",
        ]
        assert "signature: open_account(name: str, balance: float = 0.0) -> Account" in obs
        assert "docstring: Create an empty account. Extra prose." in obs

    def test_python_class_and_module_docstrings(self) -> None:
        source = (
            '"""Domain models."""\n\n\nclass Account:\n    """A user account."""\n\n    x = 1\n'
        )
        result = treesitter.extract_from_source(source, "src/models.py", "python")
        assert "docstring: Domain models." in _observations(result, "file:src/models.py")
        assert "docstring: A user account." in _observations(result, "Account (models)")

    def test_python_method_signature_omits_absent_return_annotation(self) -> None:
        source = "class C:\n    def m(self, amount):\n        pass\n"
        result = treesitter.extract_from_source(source, "c.py", "python")
        assert "signature: m(self, amount)" in _observations(result, "C.m (c)")

    def test_a_symbol_without_a_docstring_gains_no_docstring_observation(self) -> None:
        result = treesitter.extract_from_source("def f():\n    pass\n", "c.py", "python")
        assert [o for o in _observations(result, "f (c)") if o.startswith("docstring:")] == []

    def test_long_docstrings_are_truncated_to_one_line(self) -> None:
        body = "word " * 200
        source = f'def f():\n    """{body}"""\n    pass\n'
        result = treesitter.extract_from_source(source, "c.py", "python")
        doc = next(o for o in _observations(result, "f (c)") if o.startswith("docstring: "))
        assert "\n" not in doc
        assert len(doc) - len("docstring: ") == 300

    def test_typescript_signature_and_leading_block_comment(self) -> None:
        source = (
            "/** Build a reporter. */\n"
            "export function makeReporter(ledger: Ledger): Reporter {\n"
            "  return new Reporter(ledger);\n"
            "}\n"
        )
        result = treesitter.extract_from_source(source, "lib/index.ts", "typescript")
        obs = _observations(result, "makeReporter (index)")
        assert "signature: makeReporter(ledger: Ledger): Reporter" in obs
        assert "docstring: Build a reporter." in obs

    def test_typescript_class_and_method_docstrings(self) -> None:
        source = (
            "/**\n * Summarizes a ledger.\n */\n"
            "export class Reporter {\n"
            "  /** Total the entries. */\n"
            "  summarize(): string {\n    return '';\n  }\n"
            "}\n"
        )
        result = treesitter.extract_from_source(source, "lib/index.ts", "typescript")
        assert "docstring: Summarizes a ledger." in _observations(result, "Reporter (index)")
        obs = _observations(result, "Reporter.summarize (index)")
        assert "docstring: Total the entries." in obs
        assert "signature: summarize(): string" in obs

    def test_javascript_signature_and_docstring(self) -> None:
        source = "/** Format money. */\nfunction formatBalance(amount) {\n  return amount;\n}\n"
        result = treesitter.extract_from_source(source, "lib/helper.js", "javascript")
        obs = _observations(result, "formatBalance (helper)")
        assert "signature: formatBalance(amount)" in obs
        assert "docstring: Format money." in obs


class TestRationaleComments:
    """The reason a line exists lives in a NOTE/WHY comment next to it. It is
    attached to the enclosing symbol so following an edge reaches the reason."""

    def test_rationale_attaches_to_the_enclosing_symbol(self) -> None:
        source = (
            "def allows(amount):\n"
            "    # WHY: the ceiling is regulatory, not technical.\n"
            "    return amount < 10\n"
        )
        result = treesitter.extract_from_source(source, "src/policy.py", "python")
        assert (
            "rationale: [WHY] the ceiling is regulatory, not technical. (line 2)"
            in _observations(result, "allows (policy)")
        )
        assert [
            o for o in _observations(result, "file:src/policy.py") if o.startswith("rationale:")
        ] == []

    def test_module_level_rationale_attaches_to_the_file(self) -> None:
        source = "def f():\n    pass\n\n\n# TODO: make transfers atomic.\n"
        result = treesitter.extract_from_source(source, "src/policy.py", "python")
        obs = _observations(result, "file:src/policy.py")
        assert "rationale: [TODO] make transfers atomic. (line 5)" in obs

    def test_the_innermost_symbol_wins(self) -> None:
        source = "class C:\n    def m(self):\n        # HACK: retry twice.\n        pass\n"
        result = treesitter.extract_from_source(source, "c.py", "python")
        assert "rationale: [HACK] retry twice. (line 3)" in _observations(result, "C.m (c)")
        assert [o for o in _observations(result, "C (c)") if o.startswith("rationale:")] == []

    def test_citations_are_captured_separately(self) -> None:
        source = "# NOTE: not atomic yet, see ADR-0011 and RFC-42.\ndef f():\n    pass\n"
        result = treesitter.extract_from_source(source, "m.py", "python")
        obs = _observations(result, "file:m.py")
        assert "cites: ADR-0011 (line 1)" in obs
        assert "cites: RFC-42 (line 1)" in obs

    def test_javascript_slash_comments_and_block_comments(self) -> None:
        source = (
            "function f() {\n  // NOTE: keep the cast.\n  /* FIXME: slow path. */\n  return 1;\n}\n"
        )
        result = treesitter.extract_from_source(source, "a.js", "javascript")
        obs = _observations(result, "f (a)")
        assert "rationale: [NOTE] keep the cast. (line 2)" in obs
        assert "rationale: [FIXME] slow path. (line 3)" in obs

    def test_an_ordinary_comment_is_not_rationale(self) -> None:
        result = treesitter.extract_from_source("# increment i\nx = 1\n", "m.py", "python")
        assert [
            o for o in _observations(result, "file:m.py") if o.startswith(("rationale:", "cites:"))
        ] == []

    def test_rationale_text_is_capped(self) -> None:
        source = "# NOTE: " + ("long " * 100) + "\nx = 1\n"
        result = treesitter.extract_from_source(source, "m.py", "python")
        note = next(o for o in _observations(result, "file:m.py") if o.startswith("rationale:"))
        assert len(note[len("rationale: [NOTE] ") : -len(" (line 1)")]) == 200


class TestNonCodeFiles:
    """A design-token stylesheet or a config file can be the anchor of an
    invariant; if it is not in the graph, nothing can point at it."""

    def test_text_files_are_collected(self) -> None:
        files = treesitter.collect_source_files("tests/fixtures/repo")
        paths = {f["relativePath"] for f in files}
        assert "README.md" in paths
        assert "styles/tokens.css" in paths

    def test_text_files_become_root_file_entities(self) -> None:
        result = treesitter.extract_from_files(
            [{"relativePath": "styles/tokens.css", "content": ":root { --a: 1px; }\n"}]
        )
        entity = next(e for e in result["entities"] if e["name"] == "file:styles/tokens.css")
        assert entity["entityType"] == "system"
        assert entity["observations"] == [
            "File path: styles/tokens.css",
            "Language: css",
            "Symbol kind: File",
        ]
        assert entity["provenance"]["extractionMethod"] == "automated"
        assert result["relations"] == []

    def test_unrecognised_and_oversized_files_are_skipped(self, tmp_path: object) -> None:
        import os

        root = str(tmp_path)
        with open(os.path.join(root, "notes.rtf"), "w", encoding="utf-8") as handle:
            handle.write("x")
        with open(os.path.join(root, "huge.json"), "w", encoding="utf-8") as handle:
            handle.write("x" * (1024 * 1024 + 1))
        with open(os.path.join(root, "small.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
        paths = {f["relativePath"] for f in treesitter.collect_source_files(root)}
        assert paths == {"small.json"}


def _git_repo(root: str, files: dict[str, str], staged: list[str]) -> None:
    """A throwaway git work tree: every file written, only ``staged`` added."""
    import os
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for rel, content in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)
    if staged:
        subprocess.run(["git", "add", "--", *staged], cwd=root, check=True)


class TestGitVisibility:
    """Extraction reads a repository, not a directory. A gitignored file is
    private by the author's explicit instruction — sweeping it into the graph
    leaks it, so ignored paths never become entities."""

    def test_gitignored_text_file_is_not_collected(self, tmp_path: object) -> None:
        root = str(tmp_path)
        _git_repo(
            root,
            {
                ".gitignore": "SECRET.md\ndata/\n",
                "README.md": "# hi\n",
                "SECRET.md": "# private\n",
                "data/dump.json": "{}",
            },
            [".gitignore", "README.md"],
        )
        paths = {f["relativePath"] for f in treesitter.collect_source_files(root)}
        assert "README.md" in paths
        assert "SECRET.md" not in paths
        assert "data/dump.json" not in paths

    def test_untracked_text_file_is_not_collected(self, tmp_path: object) -> None:
        # The spec is git-tracked, not merely not-ignored: a non-code file
        # nobody has committed is not part of the codebase yet.
        root = str(tmp_path)
        _git_repo(root, {"README.md": "# hi\n", "notes.md": "scratch\n"}, ["README.md"])
        paths = {f["relativePath"] for f in treesitter.collect_source_files(root)}
        assert paths == {"README.md"}

    def test_gitignored_source_file_is_not_collected(self, tmp_path: object) -> None:
        root = str(tmp_path)
        _git_repo(
            root,
            {".gitignore": "generated.py\n", "keep.py": "x = 1\n", "generated.py": "y = 2\n"},
            [".gitignore", "keep.py"],
        )
        paths = {f["relativePath"] for f in treesitter.collect_source_files(root)}
        assert paths == {"keep.py"}

    def test_new_untracked_source_file_is_still_collected(self, tmp_path: object) -> None:
        # Code that is merely new (untracked but not ignored) is what someone
        # mapping a working tree most wants to see.
        root = str(tmp_path)
        _git_repo(root, {"keep.py": "x = 1\n", "fresh.py": "y = 2\n"}, ["keep.py"])
        paths = {f["relativePath"] for f in treesitter.collect_source_files(root)}
        assert paths == {"keep.py", "fresh.py"}

    def test_non_git_directory_is_walked_whole(self, tmp_path: object) -> None:
        import os

        root = str(tmp_path)
        for name in ("a.py", "b.md"):
            with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
        paths = {f["relativePath"] for f in treesitter.collect_source_files(root)}
        assert paths == {"a.py", "b.md"}


class TestExtractCodebaseDeterminism:
    def test_fixed_repo_stats(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        assert result["stats"] == {
            # 5 parsed sources + README.md and styles/tokens.css, which are
            # entities but are never parsed
            "totalFiles": 7,
            "totalSymbols": 14,
            # 21 symbols/files + the pkg:dataclasses node for the one
            # third-party import in the fixture
            "totalEntities": 22,
            "totalRelations": 23,
            # `system` counts every file entity, code or not, plus the package
            "entityBreakdown": {"system": 8, "procedure": 10, "concept": 3, "variable": 1},
            # Call edges are typed `calls`; `related_to` now means only a
            # semantic link, which structural extraction never emits. Non-code
            # files are roots: they add entities but no edges.
            "relationBreakdown": {"part_of": 17, "calls": 3, "requires": 3},
        }

    def test_fixture_repo_carries_content_not_just_coordinates(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        assert "docstring: Create an empty account." in _observations(
            result, "open_account (models)"
        )
        assert "signature: open_account(name: str) -> Account" in _observations(
            result, "open_account (models)"
        )
        policy = _observations(result, "allows (policy)")
        assert (
            "rationale: [WHY] the ceiling is regulatory, not technical — see RFC-0042. (line 8)"
            in policy
        )
        assert "cites: RFC-0042 (line 8)" in policy
        service = _observations(result, "file:src/service.py")
        assert "cites: ADR-0011 (line 17)" in service
        assert {"file:README.md", "file:styles/tokens.css"} <= {
            e["name"] for e in result["entities"]
        }

    def test_deterministic_across_runs(self) -> None:
        a = treesitter.extract_codebase("tests/fixtures/repo")
        b = treesitter.extract_codebase("tests/fixtures/repo")
        assert [e["name"] for e in a["entities"]] == [e["name"] for e in b["entities"]]
        assert [(r["from"], r["to"]) for r in a["relations"]] == [
            (r["from"], r["to"]) for r in b["relations"]
        ]

    def test_files_sorted(self) -> None:
        files = treesitter.collect_source_files("tests/fixtures/repo")
        paths = [f["relativePath"] for f in files]
        assert paths == sorted(paths)

    def test_missing_path_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="does not exist"):
            treesitter.extract_codebase("tests/fixtures/nope")


class TestExtractionLlmGating:
    def test_no_llm_selection_guard_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Selection guard fires before the LLM check.
        from theloom.errors import ValidationError
        from theloom.operations.extraction import ExtractFromDocumentsInput, extract_from_documents

        with pytest.raises(ValidationError, match="At least one of category"):
            extract_from_documents(ExtractFromDocumentsInput.model_validate({}), None)  # type: ignore[arg-type]

    def test_no_llm_configured_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from theloom.errors import ValidationError
        from theloom.operations.extraction import ExtractFromDocumentsInput, extract_from_documents

        monkeypatch.setenv("LOOM_CONFIG", "/nonexistent/x.json")
        for var in ("ANTHROPIC_API_KEY", "LOOM_LLM_PROVIDER", "LOOM_LLM_MODEL"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(ValidationError, match="No LLM configured for extraction"):
            extract_from_documents(
                ExtractFromDocumentsInput.model_validate({"category": "x"}),
                None,  # type: ignore[arg-type]
            )
