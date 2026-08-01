"""Loop / leverage-point observation parsers + Meadows levels."""

from __future__ import annotations

import re
from typing import Any

from theloom.graph.hydrate import Doc

MEADOWS_LEVELS: dict[int, dict[str, str]] = {
    12: {"name": "Parameters", "example": "Tax rates, subsidies", "depth": "parameters"},
    11: {"name": "Buffer sizes", "example": "Bank reserves, inventory", "depth": "parameters"},
    10: {
        "name": "Stock-flow structure",
        "example": "Physical infrastructure",
        "depth": "parameters",
    },
    9: {"name": "Delays", "example": "Time lags in feedback", "depth": "parameters"},
    8: {
        "name": "Negative feedback strength",
        "example": "Thermostat, market signals",
        "depth": "feedbacks",
    },
    7: {
        "name": "Positive feedback gain",
        "example": "Interest rates, erosion",
        "depth": "feedbacks",
    },
    6: {"name": "Information flows", "example": "Transparency, monitoring", "depth": "design"},
    5: {"name": "Rules", "example": "Laws, contracts, incentives", "depth": "design"},
    4: {"name": "Self-organization", "example": "Evolution, innovation", "depth": "intent"},
    3: {"name": "Goals", "example": "System purpose, objectives", "depth": "intent"},
    2: {"name": "Paradigms", "example": "Foundational assumptions", "depth": "intent"},
    1: {"name": "Transcending paradigms", "example": "Meta-awareness", "depth": "intent"},
}


def coerce_observation(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("content"), str):
        return str(value["content"])
    return "" if value is None else str(value)


def parse_loop_observations(entity: Doc) -> dict[str, Any]:
    result: dict[str, Any] = {
        "classification": None,
        "netPolarity": None,
        "memberIds": [],
        "memberCount": 0,
    }
    for raw in entity.get("observations") or []:
        observation = coerce_observation(raw)
        match = re.match(r"^classification:\s*(reinforcing|balancing)$", observation, re.I)
        if match:
            result["classification"] = match.group(1).lower()
            continue
        match = re.match(r"^net_polarity:\s*([+-])$", observation)
        if match:
            result["netPolarity"] = match.group(1)
            continue
        match = re.match(r"^member_ids:\s*(.+)$", observation)
        if match:
            result["memberIds"] = [
                part.strip() for part in match.group(1).split(",") if part.strip()
            ]
            continue
    result["memberCount"] = len(result["memberIds"])
    return result


def derive_depth_category(level: int) -> str:
    if level >= 9:
        return "parameters"
    if level >= 7:
        return "feedbacks"
    if level >= 5:
        return "design"
    return "intent"


def parse_leverage_point_observations(entity: Doc) -> dict[str, Any]:
    result: dict[str, Any] = {
        "level": None,
        "depthCategory": None,
        "meadowsName": None,
        "targetIds": [],
        "intervention": None,
        "rationale": None,
    }
    for raw in entity.get("observations") or []:
        observation = coerce_observation(raw)
        match = re.match(r"^level:\s*(\d{1,2})$", observation, re.I)
        if match:
            level = int(match.group(1))
            if 1 <= level <= 12:
                result["level"] = level
                if result["depthCategory"] is None:
                    result["depthCategory"] = derive_depth_category(level)
                if result["meadowsName"] is None and level in MEADOWS_LEVELS:
                    result["meadowsName"] = MEADOWS_LEVELS[level]["name"]
            continue
        match = re.match(
            r"^depth_category:\s*(parameters|feedbacks|design|intent)$", observation, re.I
        )
        if match:
            result["depthCategory"] = match.group(1).lower()
            continue
        match = re.match(r"^meadows_name:\s*(.+)$", observation, re.I)
        if match:
            result["meadowsName"] = match.group(1).strip()
            continue
        match = re.match(r"^target_ids:\s*(.+)$", observation, re.I)
        if match:
            result["targetIds"] = [
                part.strip() for part in match.group(1).split(",") if part.strip()
            ]
            continue
        match = re.match(r"^intervention:\s*(.+)$", observation, re.I)
        if match:
            result["intervention"] = match.group(1).strip()
            continue
        match = re.match(r"^rationale:\s*(.+)$", observation, re.I)
        if match:
            result["rationale"] = match.group(1).strip()
            continue
    return result
