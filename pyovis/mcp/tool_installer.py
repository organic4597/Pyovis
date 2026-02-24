from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolInstallResult:
    name: str
    success: bool
    message: str


class ToolInstaller:
    def __init__(self, requires_approval: bool = True) -> None:
        self.requires_approval = requires_approval

    def install(self, name: str, command: str) -> ToolInstallResult:
        if self.requires_approval:
            return ToolInstallResult(
                name=name,
                success=False,
                message="Approval required for tool installation",
            )

        return ToolInstallResult(
            name=name, success=True, message=f"Installed tool: {name}"
        )
