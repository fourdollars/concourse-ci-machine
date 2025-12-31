"""Folder Mount Manager for Concourse CI Worker.

This module provides functionality for discovering and managing folder mounts
under /srv in worker LXC containers. It handles:
- Dynamic discovery of folders before task execution
- Permission validation (read-only vs writable based on suffix)
- Mount argument generation for OCI runtime injection

Part of the general folder mounting system (Feature 001-general-mount).
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredFolder:
    """Represents a single folder under /srv ready for injection.
    
    Attributes:
        name: Folder name (e.g., 'datasets', 'models_writable')
        path: Absolute path (e.g., '/srv/datasets')
        is_writable: Whether folder ends with '_writable' or '_rw'
        permissions: Octal permission string (e.g., '0755')
        accessible: Result of permission validation
        error_message: Error details if inaccessible
    """
    name: str
    path: str
    permissions: str = "0000"
    accessible: bool = True
    error_message: Optional[str] = None
    is_writable: bool = field(init=False)

    def __post_init__(self):
        """Derive is_writable from folder name suffix."""
        self.is_writable = self.name.endswith('_writable') or self.name.endswith('_rw')


@dataclass
class MountDiscoveryResult:
    """Represents the outcome of scanning /srv for mountable folders.
    
    Attributes:
        folders: All folders found under /srv
        timestamp: When discovery was performed
        duration_ms: Time taken to complete discovery
        errors: Any validation errors encountered
    """
    folders: List[DiscoveredFolder] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: int = 0
    errors: List[str] = field(default_factory=list)
    
    def is_valid(self) -> bool:
        """Check if discovery was successful."""
        return len(self.errors) == 0 and all(f.accessible for f in self.folders)
    
    def get_folder_count(self) -> int:
        """Get total number of discovered folders."""
        return len(self.folders)
    
    def get_writable_count(self) -> int:
        """Get number of writable folders."""
        return sum(1 for f in self.folders if f.is_writable)


class FolderDiscovery:
    """Manages folder discovery and validation for mount injection.
    
    This class handles scanning /srv for folders, validating their permissions,
    and generating appropriate mount arguments for the OCI runtime wrapper.
    """
    
    def __init__(self, base_path: Path = Path("/srv")):
        """Initialize folder discovery.
        
        Args:
            base_path: Base directory to scan for folders (default: /srv)
        """
        self.base_path = base_path
        self.logger = logging.getLogger(f"{__name__}.FolderDiscovery")
    
    def scan_folders(self) -> MountDiscoveryResult:
        """Scan base_path for folders and create DiscoveredFolder instances.
        
        Returns:
            MountDiscoveryResult containing all discovered folders
        """
        start_time = datetime.now()
        result = MountDiscoveryResult()
        
        self.logger.info(f"Starting folder discovery in {self.base_path}")
        
        try:
            if not self.base_path.exists():
                error_msg = f"Base path {self.base_path} does not exist"
                self.logger.error(error_msg)
                result.errors.append(error_msg)
                return result
            
            if not self.base_path.is_dir():
                error_msg = f"Base path {self.base_path} is not a directory"
                self.logger.error(error_msg)
                result.errors.append(error_msg)
                return result
            
            # Scan for directories only (skip files, symlinks, hidden files)
            for entry in self.base_path.iterdir():
                if entry.is_dir() and not entry.name.startswith('.'):
                    folder = DiscoveredFolder(
                        name=entry.name,
                        path=str(entry)
                    )
                    result.folders.append(folder)
                    self.logger.debug(f"Discovered folder: {folder.path} (writable={folder.is_writable})")
            
            # Calculate duration
            end_time = datetime.now()
            result.duration_ms = int((end_time - start_time).total_seconds() * 1000)
            result.timestamp = start_time
            
            self.logger.info(f"Folder discovery complete: {result.get_folder_count()} folders, {result.duration_ms}ms")
            
        except Exception as e:
            error_msg = f"Folder discovery failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)
        
        return result
    
    def validate_folder(self, folder: DiscoveredFolder) -> bool:
        """Validate folder permissions.
        
        Args:
            folder: DiscoveredFolder instance to validate
        
        Returns:
            True if folder is accessible with correct permissions
        """
        path = Path(folder.path)
        
        # Check if path exists
        if not path.exists():
            folder.accessible = False
            folder.error_message = f"Path does not exist: {folder.path}"
            self.logger.error(folder.error_message)
            return False
        
        # Check if path is a directory
        if not path.is_dir():
            folder.accessible = False
            folder.error_message = f"Path is not a directory: {folder.path}"
            self.logger.error(folder.error_message)
            return False
        
        # Check read permission
        if not path.stat().st_mode & 0o444:  # Check readable
            folder.accessible = False
            folder.error_message = f"Folder not readable: {folder.path}"
            self.logger.error(folder.error_message)
            return False
        
        # Check write permission for writable folders
        if folder.is_writable:
            if not path.stat().st_mode & 0o222:  # Check writable
                folder.accessible = False
                folder.error_message = f"Writable folder not writable: {folder.path}"
                self.logger.error(folder.error_message)
                return False
        
        folder.accessible = True
        folder.permissions = oct(path.stat().st_mode)[-4:]
        return True
    
    def generate_mount_args(self, folders: List[DiscoveredFolder]) -> List[str]:
        """Generate OCI runtime mount arguments for discovered folders.
        
        Args:
            folders: List of validated DiscoveredFolder instances
        
        Returns:
            List of mount argument strings for runc/crun
        """
        mount_args = []
        
        for folder in folders:
            if not folder.accessible:
                self.logger.warning(f"Skipping inaccessible folder: {folder.path}")
                continue
            
            # Generate bind mount argument
            if folder.is_writable:
                # Writable mount (no readonly flag)
                mount_arg = f"type=bind,source={folder.path},target={folder.path}"
            else:
                # Read-only mount
                mount_arg = f"type=bind,source={folder.path},target={folder.path},readonly"
            
            mount_args.extend(["--mount", mount_arg])
            self.logger.debug(f"Generated mount arg: {mount_arg}")
        
        return mount_args
