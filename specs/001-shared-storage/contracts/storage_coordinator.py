"""
Storage Coordinator Contract
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This module defines the interface for coordinating shared storage operations
across Concourse CI Juju charm units. It enforces web/leader-only downloads
and provides worker polling mechanisms.

Contract Version: 1.0
Feature: 001-shared-storage
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional


@dataclass
class BinaryDownloadRequest:
    """Request to download Concourse binaries."""
    
    version: str  # Target version (e.g., "7.14.3")
    download_url: str  # Full URL to binary tarball
    checksum_url: str  # URL to SHA256 checksum file
    target_directory: Path  # Where to extract binaries (shared bin/)


@dataclass
class BinaryDownloadResult:
    """Result of binary download operation."""
    
    success: bool
    version: str
    installed_path: Path
    duration_seconds: float
    error_message: Optional[str] = None


class IStorageCoordinator(ABC):
    """
    Interface for coordinating shared storage operations.
    
    Implementations must enforce:
    - Web/leader-only downloads (via exclusive locks)
    - Worker polling with timeout
    - Progress marker management
    """
    
    @abstractmethod
    def is_web_leader(self) -> bool:
        """
        Check if current unit is web/leader.
        
        Returns:
            True if this unit should download binaries, False otherwise.
        """
        pass
    
    @abstractmethod
    @contextmanager
    def acquire_download_lock(self, timeout_seconds: int = 0) -> Iterator[None]:
        """
        Acquire exclusive lock for binary download (web/leader only).
        
        Args:
            timeout_seconds: Maximum time to wait for lock (0 = non-blocking)
        
        Yields:
            None when lock acquired
        
        Raises:
            LockAcquireError: If lock held by another unit
            StaleLockError: If stale lock detected
            PermissionError: If caller is not web/leader
        
        Example:
            coordinator = StorageCoordinator(...)
            if coordinator.is_web_leader():
                with coordinator.acquire_download_lock():
                    coordinator.download_binaries(request)
        """
        pass
    
    @abstractmethod
    def download_binaries(self, request: BinaryDownloadRequest) -> BinaryDownloadResult:
        """
        Download and install Concourse binaries (web/leader only).
        
        This method MUST only be called with download lock acquired.
        
        Args:
            request: Download request with version and URLs
        
        Returns:
            BinaryDownloadResult with success status
        
        Raises:
            PermissionError: If caller is not web/leader
            LockNotHeldError: If download lock not acquired
            DownloadError: If download or extraction fails
        """
        pass
    
    @abstractmethod
    def get_installed_version(self) -> Optional[str]:
        """
        Read currently installed version from marker file.
        
        Safe to call from any unit (reads shared marker).
        
        Returns:
            Version string if binaries installed, None otherwise
        """
        pass
    
    @abstractmethod
    def wait_for_binaries(
        self, 
        expected_version: str, 
        timeout_seconds: int = 300,
        poll_interval_seconds: int = 5
    ) -> bool:
        """
        Wait for binaries to be available (worker units).
        
        Workers poll for version marker with exponential backoff.
        
        Args:
            expected_version: Version to wait for
            timeout_seconds: Maximum time to wait (default: 5 minutes)
            poll_interval_seconds: Initial polling interval (default: 5 seconds)
        
        Returns:
            True if binaries available, False on timeout
        
        Example:
            if not coordinator.is_web_leader():
                if coordinator.wait_for_binaries("7.14.3", timeout=300):
                    start_worker_service()
        """
        pass
    
    @abstractmethod
    def verify_binaries(self, version: str) -> bool:
        """
        Verify that binaries for given version are valid.
        
        Checks:
        - Binary files exist in shared bin/ directory
        - Files are executable
        - Version matches marker file
        
        Args:
            version: Version to verify
        
        Returns:
            True if binaries valid, False otherwise
        """
        pass
    
    @abstractmethod
    def create_worker_directory(self, unit_name: str) -> Path:
        """
        Create isolated worker directory on shared storage.
        
        Each worker gets its own subdirectory under worker/{unit_name}/.
        
        Args:
            unit_name: Juju unit name (e.g., "concourse-ci/1")
        
        Returns:
            Path to worker directory
        
        Example:
            worker_dir = coordinator.create_worker_directory("concourse-ci/1")
            # Returns: /var/lib/concourse/worker/concourse-ci-1/
        """
        pass


class IProgressTracker(ABC):
    """
    Interface for tracking download progress.
    
    Used by web/leader to signal download state to workers.
    """
    
    @abstractmethod
    def mark_download_started(self, version: str) -> None:
        """
        Create progress marker file (web/leader only).
        
        Args:
            version: Version being downloaded
        """
        pass
    
    @abstractmethod
    def mark_download_complete(self, version: str) -> None:
        """
        Remove progress marker, write version marker (web/leader only).
        
        Args:
            version: Version successfully downloaded
        """
        pass
    
    @abstractmethod
    def is_download_in_progress(self) -> bool:
        """
        Check if download currently in progress.
        
        Safe to call from any unit.
        
        Returns:
            True if .download_in_progress marker exists
        """
        pass
    
    @abstractmethod
    def get_download_age_seconds(self) -> Optional[float]:
        """
        Get age of current download in progress.
        
        Used for stale lock detection.
        
        Returns:
            Seconds since download started, or None if no download in progress
        """
        pass


class IFilesystemValidator(ABC):
    """
    Interface for validating shared filesystem across units.
    
    Ensures all units are mounting the same shared storage.
    """
    
    @abstractmethod
    def get_filesystem_id(self, path: Path) -> str:
        """
        Get unique filesystem identifier for given path.
        
        Args:
            path: Path to check
        
        Returns:
            Filesystem ID (e.g., device UUID or inode)
        """
        pass
    
    @abstractmethod
    def validate_shared_mount(self, path: Path, expected_fs_id: str) -> bool:
        """
        Validate that path is on expected shared filesystem.
        
        Args:
            path: Path to validate
            expected_fs_id: Expected filesystem ID
        
        Returns:
            True if filesystem matches, False otherwise
        """
        pass
    
    @abstractmethod
    def is_writable(self, path: Path) -> bool:
        """
        Check if path is writable by current unit.
        
        Args:
            path: Path to check
        
        Returns:
            True if writable, False otherwise
        """
        pass


# Contract guarantees:
#
# 1. IStorageCoordinator.download_binaries() may ONLY be called by web/leader
# 2. Workers NEVER call download_binaries(), only wait_for_binaries()
# 3. acquire_download_lock() enforces single-writer via fcntl LOCK_EX
# 4. All methods raise specific exceptions (never generic Exception)
# 5. All Path parameters are absolute (no relative paths)
# 6. All timeouts are in seconds (int or float)
# 7. All version strings follow semantic versioning (e.g., "7.14.3")
