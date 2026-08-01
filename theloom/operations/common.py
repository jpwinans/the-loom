"""Shared input-model machinery for command operations.

CommandInput uses strict object schemas: unknown keys are
stripped, known keys validated. UuidStr enforces a strict UUID string so
malformed ids fail with VALIDATION_ERROR before any store work.
"""

from __future__ import annotations

import re
from typing import Annotated

import pydantic
from pydantic import AfterValidator

from theloom.model import LoomModel

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _validate_uuid(value: str) -> str:
    if not _UUID_RE.match(value):
        raise ValueError(f"Invalid uuid: {value!r}")
    return value


UuidStr = Annotated[str, AfterValidator(_validate_uuid)]


class CommandInput(LoomModel):
    """Command input base: unknown keys stripped by strict object schemas."""

    model_config = pydantic.ConfigDict(populate_by_name=True, extra="ignore")

    def provided(self, field: str) -> bool:
        """True iff the caller explicitly supplied the field (even as null) —
        the "explicitly set vs. absent" distinction. Accepts the field name or
        its wire alias (model_fields_set stores field names)."""
        if field in self.model_fields_set:
            return True
        for name, info in type(self).model_fields.items():
            if info.alias == field:
                return name in self.model_fields_set
        return False
