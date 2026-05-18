"""Integration tests for the demo workflows in src/aegis/demo.py."""

import pytest
import json
from aegis.demo import server as demo_server
from tests.conftest import AegisClient

@pytest.fixture
def server():
    """Use the server from demo.py."""
    return demo_server

@pytest.mark.asyncio
class TestDemoWorkflows:
    """Tests for workflows defined in demo.py."""

    async def test_onboard_workflow(self, server):
        """Test the onboard workflow steps using run_workflow."""
        async with AegisClient(server) as client:
            results = []
            async for res in client.run_workflow("onboard", [None, None]):
                results.append(res)
            
            assert len(results) == 3
            assert "onboarding" in results[0].lower()
            assert "comprehensive summary" in results[1].lower()
            assert "completed" in results[2].lower()

    async def test_fail_workflow(self, server):
        """Test the fail workflow error handling."""
        async with AegisClient(server) as client:
            runner = await client.start_workflow("fail")
            assert "fail intentionally" in runner.last_result.lower()
            
            result = await runner.step()
            assert "error" in result.lower()
            assert "intentional test failure" in result.lower()

    async def test_note_workflow_success(self, server, tmp_path):
        """Test the note workflow creating a file successfully."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        
        note_json = {
            "title": "Aegis Note",
            "folder": "notes",
            "filename": "aegis-test",
            "content": "This is a test note.",
            "tags": ["test"],
            "related": []
        }
        
        async with AegisClient(server) as client:
            results = []
            # Step 1: None, Step 2: None, Step 3: note_json
            async for res in client.run_workflow("note", [None, None, note_json], cwd=str(tmp_path)):
                results.append(res)
            
            assert len(results) == 4
            assert "Determine what would the user like to note" in results[0]
            assert "search the project folder" in results[1]
            assert "compose the final note content" in results[2]
            assert "created successfully" in results[3].lower()
            assert "notes/aegis-test.md" in results[3]
            
            # Verify file exists
            note_file = notes_dir / "aegis-test.md"
            assert note_file.exists()
            content = note_file.read_text()
            assert "Aegis Note" in content
            assert "This is a test note." in content

    async def test_note_workflow_validation_error_and_retry(self, server, tmp_path):
        """Test the note workflow retry logic on validation error."""
        async with AegisClient(server) as client:
            runner = await client.start_workflow("note", cwd=str(tmp_path))
            await runner.step() # Step 1: Search
            await runner.step() # Step 2: Compose (Attempt)
            
            # Provide invalid folder (doesn't exist)
            invalid_note_json = {
                "title": "Bad Note",
                "folder": "nonexistent",
                "filename": "bad",
                "content": "content",
                "tags": [],
                "related": []
            }
            
            result = await runner.step(invalid_note_json)
            
            # Should get error message about folder not existing
            assert "[ERROR]" in result
            assert "Folder does not exist" in result
            assert "2 retries remaining" in result
            
            # Fix it: create folder and send valid JSON
            notes_dir = tmp_path / "notes"
            notes_dir.mkdir()
            
            valid_note_json = {
                "title": "Good Note",
                "folder": "notes",
                "filename": "good",
                "content": "content",
                "tags": [],
                "related": []
            }
            
            result = await runner.step(valid_note_json)
            assert "created successfully" in result.lower()
            assert (notes_dir / "good.md").exists()
