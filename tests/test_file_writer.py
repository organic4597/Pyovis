import pytest
import tempfile
import shutil
from pathlib import Path
import sys
sys.path.insert(0, '/Pyvis')

from pyovis.execution.file_writer import WorkspaceManager, FileWriter


class TestWorkspaceManager:
    def test_init_with_project_id(self):
        ws = WorkspaceManager("my_project")
        assert ws.project_id == "my_project"
        assert ws.project_root.name == "my_project"

    def test_init_without_project_id(self):
        ws = WorkspaceManager()
        assert ws.project_id.startswith("project_")
        assert ws.project_root.name == ws.project_id

    def test_create_project(self):
        ws = WorkspaceManager("test_create")
        root = ws.create_project()
        assert root.exists()
        assert root.is_dir()
        ws.delete_project()

    def test_create_project_with_structure(self):
        ws = WorkspaceManager("test_structure")
        structure = ["src/main.py", "src/utils.py", "requirements.txt"]
        root = ws.create_project(structure)
        
        assert (root / "src" / "main.py").exists()
        assert (root / "src" / "utils.py").exists()
        assert (root / "requirements.txt").exists()
        
        ws.delete_project()

    def test_write_file(self):
        ws = WorkspaceManager("test_write")
        ws.create_project()
        
        path = ws.write_file("test.py", "print('hello')")
        assert path.exists()
        assert path.read_text() == "print('hello')"
        
        ws.delete_project()

    def test_read_file(self):
        ws = WorkspaceManager("test_read")
        ws.create_project()
        ws.write_file("test.py", "content")
        
        content = ws.read_file("test.py")
        assert content == "content"
        
        ws.delete_project()

    def test_read_nonexistent_file(self):
        ws = WorkspaceManager("test_read_none")
        ws.create_project()
        
        content = ws.read_file("nonexistent.py")
        assert content is None
        
        ws.delete_project()

    def test_file_exists(self):
        ws = WorkspaceManager("test_exists")
        ws.create_project()
        ws.write_file("exists.py", "content")
        
        assert ws.file_exists("exists.py") is True
        assert ws.file_exists("not_exists.py") is False
        
        ws.delete_project()

    def test_list_files(self):
        ws = WorkspaceManager("test_list")
        ws.create_project(["a.py", "b.py", "sub/c.py"])
        
        files = ws.list_files()
        file_names = [f.name for f in files if f.is_file()]
        
        assert "a.py" in file_names
        assert "b.py" in file_names
        assert "c.py" in file_names
        
        ws.delete_project()

    def test_delete_project(self):
        ws = WorkspaceManager("test_delete")
        ws.create_project()
        root = ws.project_root
        
        assert root.exists()
        ws.delete_project()
        assert not root.exists()

    def test_get_project_info(self):
        ws = WorkspaceManager("test_info")
        ws.create_project(["main.py"])
        
        info = ws.get_project_info()
        
        assert info["project_id"] == "test_info"
        assert info["file_count"] == 1
        assert "main.py" in info["files"]
        
        ws.delete_project()


class TestFileWriter:
    @pytest.fixture
    def workspace(self):
        ws = WorkspaceManager("test_writer")
        ws.create_project()
        yield ws
        ws.delete_project()

    @pytest.fixture
    def writer(self, workspace):
        return FileWriter(workspace)

    def test_save_code(self, writer):
        result = writer.save_code("test.py", "print('hello')")
        
        assert result["status"] == "saved"
        assert result["size_bytes"] > 0
        assert "test.py" in result["path"]

    def test_save_multiple(self, writer):
        files = [
            {"path": "a.py", "content": "a"},
            {"path": "b.py", "content": "bb"},
        ]
        results = writer.save_multiple(files)
        
        assert len(results) == 2
        assert all(r["status"] == "saved" for r in results)

    def test_append_to_file(self, writer):
        writer.save_code("test.py", "line1")
        result = writer.append_to_file("test.py", "line2")
        
        assert result["status"] == "saved"
        
        content = writer.workspace.read_file("test.py")
        assert "line1" in content
        assert "line2" in content

    def test_append_to_nonexistent_file(self, writer):
        result = writer.append_to_file("new.py", "content")
        
        assert result["status"] == "saved"
        assert writer.workspace.file_exists("new.py")

    def test_create_directory(self, writer):
        result = writer.create_directory("subdir/nested")
        
        assert result["status"] == "created"
        assert (writer.workspace.project_root / "subdir" / "nested").is_dir()


class TestIntegration:
    def test_full_workflow(self):
        ws = WorkspaceManager("integration_test")
        fw = FileWriter(ws)
        
        ws.create_project(["src/__init__.py"])
        
        fw.save_code("src/main.py", '''
def main():
    print("Hello World")

if __name__ == "__main__":
    main()
''')
        
        fw.save_code("requirements.txt", "requests>=2.0\n")
        
        info = ws.get_project_info()
        assert info["file_count"] >= 2
        
        content = ws.read_file("src/main.py")
        assert "def main()" in content
        
        ws.delete_project()
