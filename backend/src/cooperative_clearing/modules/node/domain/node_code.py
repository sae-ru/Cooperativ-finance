"""Stable human-readable node identifier."""

import re
from dataclasses import dataclass

_NODE_CODE = re.compile(r"^[a-z][a-z0-9-]{2,62}$")


@dataclass(frozen=True, slots=True)
class NodeCode:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if _NODE_CODE.fullmatch(normalized) is None or "--" in normalized:
            raise ValueError("invalid node code")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value
