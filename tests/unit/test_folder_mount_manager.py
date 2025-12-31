"""Unit tests for folder mount manager.

Tests the FolderDiscovery class and related functionality for discovering
and validating folders under /srv.
"""

import pytest
from pathlib import Path
from datetime import datetime
import tempfile
import os

# Import the module to test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))
from folder_mount_manager import FolderDiscovery, DiscoveredFolder, MountDiscoveryResult


class TestDiscoveredFolder:
    """Test DiscoveredFolder dataclass."""
    
    def test_create_readonly_folder(self):
        """Test creating a read-only folder (no suffix)."""
        folder = DiscoveredFolder(name="datasets", path="/srv/datasets")
        assert folder.name == "datasets"
        assert folder.path == "/srv/datasets"
        assert folder.is_writable is False
        assert folder.accessible is True
    
    def test_create_writable_folder_with_writable_suffix(self):
        """Test creating a writable folder with _writable suffix."""
        folder = DiscoveredFolder(name="outputs_writable", path="/srv/outputs_writable")
        assert folder.name == "outputs_writable"
        assert folder.is_writable is True
    
    def test_create_writable_folder_with_rw_suffix(self):
        """Test creating a writable folder with _rw suffix."""
        folder = DiscoveredFolder(name="cache_rw", path="/srv/cache_rw")
        assert folder.name == "cache_rw"
        assert folder.is_writable is True
    
    def test_folder_name_with_suffix_in_middle(self):
        """Test that suffix must be at the end."""
        folder = DiscoveredFolder(name="data_writable_backup", path="/srv/data_writable_backup")
        # This should NOT be writable because suffix is not at the end
        assert folder.is_writable is False


class TestMountDiscoveryResult:
    """Test MountDiscoveryResult dataclass."""
    
    def test_empty_result_is_valid(self):
        """Test that empty result with no errors is valid."""
        result = MountDiscoveryResult()
        assert result.is_valid() is True
        assert result.get_folder_count() == 0
        assert result.get_writable_count() == 0
    
    def test_result_with_errors_is_invalid(self):
        """Test that result with errors is invalid."""
        result = MountDiscoveryResult(errors=["Test error"])
        assert result.is_valid() is False
    
    def test_result_with_inaccessible_folder_is_invalid(self):
        """Test that result with inaccessible folder is invalid."""
        folder = DiscoveredFolder(name="test", path="/srv/test", accessible=False)
        result = MountDiscoveryResult(folders=[folder])
        assert result.is_valid() is False
    
    def test_folder_counts(self):
        """Test folder counting methods."""
        folders = [
            DiscoveredFolder(name="datasets", path="/srv/datasets"),
            DiscoveredFolder(name="models", path="/srv/models"),
            DiscoveredFolder(name="outputs_writable", path="/srv/outputs_writable"),
            DiscoveredFolder(name="cache_rw", path="/srv/cache_rw"),
        ]
        result = MountDiscoveryResult(folders=folders)
        assert result.get_folder_count() == 4
        assert result.get_writable_count() == 2


class TestFolderDiscovery:
    """Test FolderDiscovery class."""
    
    @pytest.fixture
    def temp_srv(self):
        """Create a temporary /srv-like directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            yield tmppath
    
    def test_scan_empty_directory(self, temp_srv):
        """Test scanning an empty directory."""
        discovery = FolderDiscovery(base_path=temp_srv)
        result = discovery.scan_folders()
        
        assert result.is_valid() is True
        assert result.get_folder_count() == 0
        assert result.duration_ms >= 0
    
    def test_scan_with_readonly_folders(self, temp_srv):
        """Test scanning directory with read-only folders."""
        # Create test folders
        (temp_srv / "datasets").mkdir()
        (temp_srv / "models").mkdir()
        
        discovery = FolderDiscovery(base_path=temp_srv)
        result = discovery.scan_folders()
        
        assert result.is_valid() is True
        assert result.get_folder_count() == 2
        assert result.get_writable_count() == 0
        
        folder_names = [f.name for f in result.folders]
        assert "datasets" in folder_names
        assert "models" in folder_names
    
    def test_scan_with_writable_folders(self, temp_srv):
        """Test scanning directory with writable folders."""
        # Create test folders with writable suffixes
        (temp_srv / "outputs_writable").mkdir()
        (temp_srv / "cache_rw").mkdir()
        
        discovery = FolderDiscovery(base_path=temp_srv)
        result = discovery.scan_folders()
        
        assert result.is_valid() is True
        assert result.get_folder_count() == 2
        assert result.get_writable_count() == 2
    
    def test_scan_skips_files(self, temp_srv):
        """Test that scanning skips regular files."""
        # Create a folder and a file
        (temp_srv / "datasets").mkdir()
        (temp_srv / "readme.txt").write_text("test")
        
        discovery = FolderDiscovery(base_path=temp_srv)
        result = discovery.scan_folders()
        
        # Should only find the folder, not the file
        assert result.get_folder_count() == 1
        assert result.folders[0].name == "datasets"
    
    def test_scan_skips_hidden_folders(self, temp_srv):
        """Test that scanning skips hidden folders (starting with .)."""
        # Create visible and hidden folders
        (temp_srv / "datasets").mkdir()
        (temp_srv / ".hidden").mkdir()
        
        discovery = FolderDiscovery(base_path=temp_srv)
        result = discovery.scan_folders()
        
        # Should only find visible folder
        assert result.get_folder_count() == 1
        assert result.folders[0].name == "datasets"
    
    def test_scan_nonexistent_path(self):
        """Test scanning a non-existent path."""
        discovery = FolderDiscovery(base_path=Path("/nonexistent/path"))
        result = discovery.scan_folders()
        
        assert result.is_valid() is False
        assert len(result.errors) > 0
        assert "does not exist" in result.errors[0].lower()
    
    def test_validate_accessible_folder(self, temp_srv):
        """Test validating an accessible folder."""
        folder_path = temp_srv / "datasets"
        folder_path.mkdir()
        
        folder = DiscoveredFolder(name="datasets", path=str(folder_path))
        discovery = FolderDiscovery(base_path=temp_srv)
        
        assert discovery.validate_folder(folder) is True
        assert folder.accessible is True
        assert folder.error_message is None
    
    def test_validate_nonexistent_folder(self):
        """Test validating a non-existent folder."""
        folder = DiscoveredFolder(name="missing", path="/srv/missing")
        discovery = FolderDiscovery()
        
        assert discovery.validate_folder(folder) is False
        assert folder.accessible is False
        assert folder.error_message is not None
    
    def test_generate_mount_args_readonly(self, temp_srv):
        """Test generating mount arguments for read-only folders."""
        folder = DiscoveredFolder(name="datasets", path="/srv/datasets", accessible=True)
        discovery = FolderDiscovery()
        
        mount_args = discovery.generate_mount_args([folder])
        
        assert len(mount_args) == 2
        assert mount_args[0] == "--mount"
        assert "type=bind" in mount_args[1]
        assert "readonly" in mount_args[1]
        assert "/srv/datasets" in mount_args[1]
    
    def test_generate_mount_args_writable(self, temp_srv):
        """Test generating mount arguments for writable folders."""
        folder = DiscoveredFolder(name="outputs_writable", path="/srv/outputs_writable", accessible=True)
        discovery = FolderDiscovery()
        
        mount_args = discovery.generate_mount_args([folder])
        
        assert len(mount_args) == 2
        assert mount_args[0] == "--mount"
        assert "type=bind" in mount_args[1]
        assert "readonly" not in mount_args[1]
        assert "/srv/outputs_writable" in mount_args[1]
    
    def test_generate_mount_args_mixed(self, temp_srv):
        """Test generating mount arguments for mixed folders."""
        folders = [
            DiscoveredFolder(name="datasets", path="/srv/datasets", accessible=True),
            DiscoveredFolder(name="outputs_writable", path="/srv/outputs_writable", accessible=True),
        ]
        discovery = FolderDiscovery()
        
        mount_args = discovery.generate_mount_args(folders)
        
        # Should have 4 args total (2 folders * 2 args each)
        assert len(mount_args) == 4
        # Check readonly present in first mount
        assert "readonly" in mount_args[1]
        # Check readonly NOT present in second mount
        assert "readonly" not in mount_args[3]
    
    def test_generate_mount_args_skips_inaccessible(self):
        """Test that inaccessible folders are skipped in mount args."""
        folders = [
            DiscoveredFolder(name="datasets", path="/srv/datasets", accessible=True),
            DiscoveredFolder(name="missing", path="/srv/missing", accessible=False),
        ]
        discovery = FolderDiscovery()
        
        mount_args = discovery.generate_mount_args(folders)
        
        # Should only have args for the accessible folder
        assert len(mount_args) == 2
        assert "/srv/datasets" in mount_args[1]
        assert "/srv/missing" not in mount_args[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
