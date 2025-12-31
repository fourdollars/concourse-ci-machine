"""Folder Mounts Library for Concourse CI Machine Charm.

This library provides reusable functionality for managing folder mounts
in Concourse worker LXC containers. It can be used by other charms or
components that need similar folder mounting capabilities.

Usage:
    from charms.concourse_ci_machine.v0.folder_mounts import FolderMountManager
    
    manager = FolderMountManager()
    result = manager.discover_and_validate()
    if result.is_valid():
        mount_args = manager.generate_mount_arguments(result)
"""

# The unique Charmhub library identifier, never change it
LIBID = "placeholder-generate-on-publish"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

from pathlib import Path
from typing import List, Optional
import logging

# Import from the main module
import sys
sys.path.append('/var/lib/juju/agents/unit-concourse-ci-machine-0/charm/src')
from folder_mount_manager import FolderDiscovery, DiscoveredFolder, MountDiscoveryResult

logger = logging.getLogger(__name__)


class FolderMountManager:
    """High-level interface for folder mount management.
    
    This class provides a simplified API for discovering folders,
    validating permissions, and generating mount arguments for
    OCI runtime injection.
    
    Example:
        manager = FolderMountManager(base_path="/srv")
        result = manager.discover_and_validate()
        
        if not result.is_valid():
            # Handle errors
            for error in result.errors:
                logger.error(error)
            return
        
        mount_args = manager.generate_mount_arguments(result)
        # Use mount_args with OCI runtime
    """
    
    def __init__(self, base_path: str = "/srv", timeout_seconds: int = 180):
        """Initialize folder mount manager.
        
        Args:
            base_path: Base directory to scan for folders (default: /srv)
            timeout_seconds: Maximum time allowed for discovery (default: 180)
        """
        self.base_path = Path(base_path)
        self.timeout_seconds = timeout_seconds
        self.discovery = FolderDiscovery(self.base_path)
        self.logger = logging.getLogger(f"{__name__}.FolderMountManager")
    
    def discover_and_validate(self) -> MountDiscoveryResult:
        """Discover folders and validate their permissions.
        
        This is the primary entry point for folder discovery. It scans the
        base path, creates DiscoveredFolder instances, and validates each
        folder's permissions.
        
        Returns:
            MountDiscoveryResult containing all discovered and validated folders
        """
        self.logger.info(f"Starting folder discovery and validation in {self.base_path}")
        
        # Discover folders
        result = self.discovery.scan_folders()
        
        if result.errors:
            self.logger.error(f"Discovery encountered {len(result.errors)} errors")
            return result
        
        # Validate each folder
        for folder in result.folders:
            if not self.discovery.validate_folder(folder):
                error_msg = f"Validation failed for {folder.path}: {folder.error_message}"
                result.errors.append(error_msg)
                self.logger.error(error_msg)
        
        # Check timeout
        if result.duration_ms > (self.timeout_seconds * 1000):
            error_msg = f"Discovery timeout: {result.duration_ms}ms exceeds {self.timeout_seconds}s"
            result.errors.append(error_msg)
            self.logger.error(error_msg)
        
        self.logger.info(
            f"Discovery complete: {result.get_folder_count()} folders "
            f"({result.get_writable_count()} writable), "
            f"{len(result.errors)} errors"
        )
        
        return result
    
    def generate_mount_arguments(self, result: MountDiscoveryResult) -> List[str]:
        """Generate OCI runtime mount arguments.
        
        Args:
            result: MountDiscoveryResult from discover_and_validate()
        
        Returns:
            List of mount argument strings for runc/crun
        """
        if not result.is_valid():
            self.logger.warning("Generating mount args for result with errors")
        
        mount_args = self.discovery.generate_mount_args(result.folders)
        self.logger.info(f"Generated {len(mount_args)//2} mount arguments")
        
        return mount_args
    
    def get_discovery_status(self, result: MountDiscoveryResult) -> str:
        """Get human-readable status string for discovery result.
        
        Args:
            result: MountDiscoveryResult to format
        
        Returns:
            Status string suitable for charm status messages
        """
        if not result.is_valid():
            return f"Folder discovery failed: {'; '.join(result.errors[:3])}"
        
        folder_count = result.get_folder_count()
        writable_count = result.get_writable_count()
        readonly_count = folder_count - writable_count
        
        return (
            f"Folder discovery complete: {folder_count} folders "
            f"({readonly_count} read-only, {writable_count} writable)"
        )
