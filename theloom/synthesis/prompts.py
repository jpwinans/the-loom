"""Prompt utilities and format prompts (FORMAT_PROMPTS, code-fence stripping)."""

from __future__ import annotations

import re
from typing import Any

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_XML_BRACKETS = re.compile(r"[<>]")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*\n?([\s\S]*?)\n?\s*```$")


def _js_string(value: Any) -> str:
    """JS String(x) for the non-string values that can reach sanitize:
    None -> '', dict -> '[object Object]', list -> comma-joined elements."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return "[object Object]"
    if isinstance(value, list):
        return ",".join(_js_string(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def sanitize_for_prompt(text: Any, max_len: int = 200) -> str:
    """Strip control chars and angle brackets, hard-cut at max_len (no ellipsis)."""
    raw = text if isinstance(text, str) else _js_string(text)
    cleaned = _CONTROL_CHARS.sub(" ", raw)
    no_xml = _XML_BRACKETS.sub("", cleaned)
    return no_xml[:max_len] if len(no_xml) > max_len else no_xml


def strip_code_fences(text: str) -> str:
    trimmed = text.strip()
    match = _CODE_FENCE.match(trimmed)
    return match.group(1) if match else trimmed


FORMAT_PROMPTS: dict[str, str] = {
    "narrative": (
        "Write a coherent narrative that synthesizes the provided knowledge graph data. "
        "Each paragraph should flow naturally into the next. Include citations by "
        "referencing entity names."
    ),
    "outline": (
        "Create a structured outline from the provided knowledge graph data. Use "
        "hierarchical headings (##, ###) and bullet points. Group related concepts together."
    ),
    "evidence_map": (
        "Create an evidence map showing the relationship between claims, evidence, and "
        "sources. Use a structured format with clear attribution."
    ),
    "causal_chain": (
        "Describe the causal chain of events/relationships. Show how each cause leads to "
        "its effects. Use directional language (leads to, causes, enables)."
    ),
    "raw": "",
    "proposal": (
        "Given this subgraph, identify structural problems and propose specific "
        "interventions. For each proposal, specify: what to add/modify/remove, expected "
        'impact, and which invariants it satisfies. Return a JSON object with a "proposals" '
        'array. Each proposal must have: "action" (one of "add_entity", "add_relation", '
        '"modify", "remove"), "rationale" (why this change is needed), "expectedImpact" '
        '(what improvement is expected). Optionally include: "entitySpec" (with "name", '
        '"entityType", "observations" array), "relationSpec" (with "from", "to", '
        '"relationType"), "addressesViolation" (which structural violation this addresses). '
        "Reference specific entity and relation names from the input subgraph."
    ),
}
