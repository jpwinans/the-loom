"""Entity-chunk link lookups.

These links live in process memory, populated by document ingestion. In a fresh
CLI process nothing has been ingested yet, so every entity has no links and the
lookups below return empty.
"""

from __future__ import annotations

from typing import Any


def get_links_for_entity(entity_id: str) -> list[dict[str, Any]]:
    return []


def get_source_passages(entity_id: str) -> list[str]:
    """Evidence strings from links."""
    return [
        link["evidence"]
        for link in get_links_for_entity(entity_id)
        if link.get("evidence") and len(link["evidence"]) > 0
    ]
