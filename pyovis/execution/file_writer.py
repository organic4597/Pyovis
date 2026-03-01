from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


class WorkspaceManager:
    WORKSPACE_ROOT = Path("/pyovis_memory/workspace")
    
    def __init__(self, project_id: str | None = None):
        self.project_id = project_id or self._generate_project_id()
        self.project_root = self.WORKSPACE_ROOT / self.project_id
        
    def _generate_project_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"project_{timestamp}"
    
    def create_project(self, structure: list[str] | None = None) -> Path:
        """Create project directory structure (directories only, no empty files)."""
        self.project_root.mkdir(parents=True, exist_ok=True)
        
        if structure:
            for file_path in structure:
                full_path = self.project_root / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
        
        return self.project_root
    
    def get_file_path(self, relative_path: str) -> Path:
        return self.project_root / relative_path
    
    def write_file(self, relative_path: str, content: str) -> Path:
        full_path = self.project_root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return full_path
    
    def read_file(self, relative_path: str) -> str | None:
        full_path = self.project_root / relative_path
        if full_path.exists():
            return full_path.read_text(encoding="utf-8")
        return None
    
    def file_exists(self, relative_path: str) -> bool:
        return (self.project_root / relative_path).exists()
    
    def list_files(self, pattern: str = "**/*") -> list[Path]:
        return list(self.project_root.glob(pattern))
    
    def delete_project(self) -> None:
        if self.project_root.exists():
            shutil.rmtree(self.project_root)
    
    def get_project_info(self) -> dict:
        files = self.list_files()
        return {
            "project_id": self.project_id,
            "root": str(self.project_root),
            "file_count": len([f for f in files if f.is_file()]),
            "files": [str(f.relative_to(self.project_root)) for f in files if f.is_file()]
        }


class FileWriter:
    def __init__(self, workspace: WorkspaceManager):
        self.workspace = workspace
    
    def save_code(self, file_path: str, code: str) -> dict:
        saved_path = self.workspace.write_file(file_path, code)
        return {
            "status": "saved",
            "path": str(saved_path),
            "size_bytes": len(code.encode("utf-8"))
        }
    
    def save_multiple(self, files: list[dict]) -> list[dict]:
        results = []
        for file_info in files:
            path = file_info.get("path", "output.py")
            content = file_info.get("content", "")
            result = self.save_code(path, content)
            results.append(result)
        return results
    
    def append_to_file(self, file_path: str, content: str) -> dict:
        existing = self.workspace.read_file(file_path) or ""
        new_content = existing + "\n" + content
        return self.save_code(file_path, new_content)
    
    def create_directory(self, dir_path: str) -> dict:
        full_path = self.workspace.project_root / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        return {"status": "created", "path": str(full_path)}
