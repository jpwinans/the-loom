"""TL-486 round-2 gap (integration arbiter, Agent Contract epic TL-477):
``inference-rule-create``'s ``?var`` rule-variable syntax was documented in
NONE of ``--schema``, ``--help``, or COMMANDS.md — ``rule.conditions[].from``/
``.to`` showed up as bare ``{"title": "From", "type": "string"}``. An agent
guessing a bare name or a wrong sigil (``$a``, ``A``) got an identical
success-shaped response and a permanently inert rule: a formal
discoverability failure, the exact thing TL-486 exists to eliminate.

These tests pin that ``RuleCondition``/``RuleConclusion``/``RuleSpec`` fields
now carry ``Field(description=...)`` teaching the ``?name`` convention,
end to end through the model, the ``--schema`` JSON output, and the
COMMANDS.md field table — without adding any new validation (TL-495's job,
not this one: a bare non-id string must still validate cleanly here).
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from theloom.cli.app import app
from theloom.cli.docs import generate_docs
from theloom.operations.inference import (
    ExplainInferenceInput,
    RuleConclusion,
    RuleCondition,
    RuleSpec,
    RunInferenceInput,
)

runner = CliRunner()


def _desc(model: type, field: str) -> str:
    info = model.model_fields[field]
    assert info.description, f"{model.__name__}.{field} has no description"
    return info.description


def test_rule_condition_from_and_to_teach_the_variable_syntax() -> None:
    for field in ("from_", "to"):
        text = _desc(RuleCondition, field).lower()
        assert "?" in text
        assert "variable" in text
        assert "literal" in text


def test_rule_condition_from_documents_the_inert_rule_pitfall_without_adding_validation() -> None:
    """The description must say a bare/wrong-sigil string is legal but inert
    — it must NOT claim (or imply via test) that such input is rejected;
    TL-495 owns adding that validation, not this fix."""
    text = _desc(RuleCondition, "from_")
    assert "never match" in text.lower() or "inert" in text.lower()
    # No validation added: a bare name still constructs cleanly.
    RuleCondition.model_validate({"from": "A", "to": "?b", "relationType": "enables"})
    RuleCondition.model_validate({"from": "$a", "to": "?b", "relationType": "enables"})


def test_rule_condition_and_conclusion_include_a_worked_example() -> None:
    combined = " ".join(
        [_desc(RuleCondition, "from_"), _desc(RuleCondition, "to"), _desc(RuleSpec, "conditions")]
    )
    assert "?a" in combined and "?b" in combined and "?c" in combined
    assert "enables" in combined


def test_rule_conclusion_from_and_to_teach_the_variable_syntax() -> None:
    for field in ("from_", "to"):
        text = _desc(RuleConclusion, field).lower()
        assert "?" in text
        assert "variable" in text or "bound" in text


def test_rule_spec_fields_are_documented() -> None:
    for field in ("name", "description", "conditions", "conclusion", "enabled"):
        _desc(RuleSpec, field)


def test_run_inference_rule_id_and_explain_inference_relation_id_are_documented() -> None:
    """Round-2 also asked to check these two commands for the same blind
    spot; neither carries rule-shape fields, but their one addressing field
    should still say where its value comes from."""
    assert RunInferenceInput.model_fields["rule_id"].description
    assert ExplainInferenceInput.model_fields["relation_id"].description


def test_schema_flag_output_teaches_the_variable_syntax_without_source() -> None:
    """The exact surface the arbiter used: --schema alone, no source read."""
    result = runner.invoke(app, ["inference-rule-create", "--schema"])
    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    condition_from = schema["$defs"]["RuleCondition"]["properties"]["from"]
    condition_to = schema["$defs"]["RuleCondition"]["properties"]["to"]
    conclusion_from = schema["$defs"]["RuleConclusion"]["properties"]["from"]
    for prop in (condition_from, condition_to, conclusion_from):
        assert "description" in prop, prop
        assert "?" in prop["description"]


def test_commands_md_field_table_teaches_the_variable_syntax() -> None:
    text = generate_docs()
    start = text.index("`inference-rule-create`")
    end = text.find("\n- **`", start)
    block = text[start:end]
    from_line = next(line for line in block.splitlines() if "`rule.conditions[].from`" in line)
    assert "?" in from_line and "variable" in from_line.lower()
