from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolRecord:
    name: str
    description: str
    requires_approval: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRecord] = {}

    def register(self, name: str, description: str, requires_approval: bool = True) -> None:
        self._tools[name] = ToolRecord(
            name=name, description=description, requires_approval=requires_approval
        )

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def list_tools(self) -> list[ToolRecord]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolRecord | None:
        return self._tools.get(name)
