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
        # open_account calls Account (both resolve) -> related_to
        related = [r for r in result["relations"] if r["relationType"] == "related_to"]
        assert any(
            r["from"] == "open_account (models)" and r["to"] == "Account (models)" for r in related
        )

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

    def test_import_becomes_requires(self) -> None:
        result = treesitter.extract_from_source(
            "from dataclasses import dataclass\n", "m.py", "python"
        )
        requires = [r for r in result["relations"] if r["relationType"] == "requires"]
        assert requires[0]["to"] == "dataclasses"


class TestExtractCodebaseDeterminism:
    def test_fixed_repo_stats(self) -> None:
        result = treesitter.extract_codebase("tests/fixtures/repo")
        assert result["stats"] == {
            "totalFiles": 4,
            "totalSymbols": 12,
            "totalEntities": 16,
            "totalRelations": 19,
            "entityBreakdown": {"system": 4, "procedure": 9, "concept": 3},
            "relationBreakdown": {"part_of": 15, "requires": 3, "related_to": 1},
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
