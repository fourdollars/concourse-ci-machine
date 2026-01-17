"""
Upgrade Protocol Contract
~~~~~~~~~~~~~~~~~~~~~~~~~

This module defines the protocol for coordinating upgrades across Concourse CI
units via Juju peer relations. Web/leader orchestrates, workers respond.

Contract Version: 1.0
Feature: 001-shared-storage
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Optional


class UpgradePhase(Enum):
    """Upgrade coordination phases."""
    
    IDLE = "idle"  # No upgrade in progress
    PREPARE = "prepare"  # Web/leader initiating, workers should stop
    DOWNLOADING = "downloading"  # Web/leader downloading binaries
    COMPLETE = "complete"  # Download complete, workers should start


@dataclass
class UpgradeCoordinationState:
    """
    State stored in peer relation data during upgrade.
    
    This is the canonical schema for upgrade coordination.
    """
    
    # Current upgrade phase
    phase: UpgradePhase
    
    # Target version being upgraded to
    target_version: Optional[str] = None
    
    # Unit that initiated upgrade (typically web/leader)
    initiated_by: Optional[str] = None
    
    # Timestamp of phase change (UTC)
    timestamp: datetime = None
    
    # Number of workers that acknowledged prepare signal
    worker_ready_count: int = 0
    
    # Expected number of workers (excludes web/leader)
    expected_worker_count: int = 0
    
    def to_relation_data(self) -> dict[str, str]:
        """
        Serialize to peer relation data format.
        
        All values must be strings (Juju relation data constraint).
        
        Returns:
            Dict suitable for relation.data[unit][key] = value
        """
        return {
            "upgrade-phase": self.phase.value,
            "target-version": self.target_version or "",
            "initiated-by": self.initiated_by or "",
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "worker-ready-count": str(self.worker_ready_count),
            "expected-worker-count": str(self.expected_worker_count),
        }
    
    @classmethod
    def from_relation_data(cls, data: dict[str, str]) -> "UpgradeCoordinationState":
        """
        Deserialize from peer relation data.
        
        Args:
            data: Dict from relation.data[unit]
        
        Returns:
            UpgradeCoordinationState instance
        
        Raises:
            ValueError: If data format invalid
        """
        phase = UpgradePhase(data.get("upgrade-phase", "idle"))
        timestamp_str = data.get("timestamp")
        
        return cls(
            phase=phase,
            target_version=data.get("target-version") or None,
            initiated_by=data.get("initiated-by") or None,
            timestamp=datetime.fromisoformat(timestamp_str) if timestamp_str else None,
            worker_ready_count=int(data.get("worker-ready-count", 0)),
            expected_worker_count=int(data.get("expected-worker-count", 0)),
        )


@dataclass
class WorkerAcknowledgment:
    """
    Worker's acknowledgment of upgrade signals.
    
    Stored in worker unit's peer relation data.
    """
    
    # Worker unit name
    unit_name: str
    
    # Whether worker has stopped service and is ready for upgrade
    upgrade_ready: bool = False
    
    # Timestamp of acknowledgment (UTC)
    timestamp: Optional[datetime] = None
    
    def to_relation_data(self) -> dict[str, str]:
        """Serialize to peer relation data format."""
        return {
            "upgrade-ready": str(self.upgrade_ready).lower(),
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
        }
    
    @classmethod
    def from_relation_data(cls, unit_name: str, data: dict[str, str]) -> "WorkerAcknowledgment":
        """Deserialize from peer relation data."""
        timestamp_str = data.get("timestamp")
        
        return cls(
            unit_name=unit_name,
            upgrade_ready=data.get("upgrade-ready", "false").lower() == "true",
            timestamp=datetime.fromisoformat(timestamp_str) if timestamp_str else None,
        )


class IUpgradeCoordinator(ABC):
    """
    Interface for coordinating upgrades via peer relations.
    
    Web/leader implements orchestration logic.
    Workers implement response handlers.
    """
    
    @abstractmethod
    def initiate_upgrade(self, target_version: str) -> None:
        """
        Initiate upgrade process (web/leader only).
        
        Steps:
        1. Set phase=PREPARE in peer relation
        2. Set target_version and expected_worker_count
        3. Trigger relation-changed on all units
        
        Args:
            target_version: Version to upgrade to
        
        Raises:
            PermissionError: If caller is not web/leader
            UpgradeInProgressError: If upgrade already in progress
        """
        pass
    
    @abstractmethod
    def wait_for_workers_ready(self, timeout_seconds: int = 120) -> bool:
        """
        Wait for all workers to acknowledge prepare signal (web/leader only).
        
        Polls peer relation data for worker acknowledgments.
        
        Args:
            timeout_seconds: Maximum time to wait (default: 2 minutes)
        
        Returns:
            True if all workers ready, False on timeout
        
        Raises:
            PermissionError: If caller is not web/leader
        """
        pass
    
    @abstractmethod
    def mark_download_phase(self) -> None:
        """
        Set phase=DOWNLOADING in peer relation (web/leader only).
        
        Called before starting binary download.
        
        Raises:
            PermissionError: If caller is not web/leader
        """
        pass
    
    @abstractmethod
    def complete_upgrade(self) -> None:
        """
        Set phase=COMPLETE in peer relation (web/leader only).
        
        Called after binaries downloaded and web/leader service restarted.
        Workers will detect this and restart their services.
        
        Raises:
            PermissionError: If caller is not web/leader
        """
        pass
    
    @abstractmethod
    def reset_upgrade_state(self) -> None:
        """
        Reset to phase=IDLE (web/leader only).
        
        Called after all workers have restarted.
        
        Raises:
            PermissionError: If caller is not web/leader
        """
        pass
    
    @abstractmethod
    def get_upgrade_state(self) -> UpgradeCoordinationState:
        """
        Read current upgrade state from peer relation.
        
        Safe to call from any unit.
        
        Returns:
            Current upgrade coordination state
        """
        pass
    
    @abstractmethod
    def handle_prepare_signal(self) -> None:
        """
        Handle PREPARE signal from web/leader (worker units only).
        
        Steps:
        1. Stop concourse-worker.service
        2. Set upgrade-ready=true in peer relation
        3. Wait for COMPLETE signal
        
        Raises:
            PermissionError: If caller is web/leader
            ServiceManagementError: If service stop fails
        """
        pass
    
    @abstractmethod
    def handle_complete_signal(self) -> None:
        """
        Handle COMPLETE signal from web/leader (worker units only).
        
        Steps:
        1. Verify new binaries are installed
        2. Start concourse-worker.service
        3. Clear upgrade-ready flag
        
        Raises:
            PermissionError: If caller is web/leader
            ServiceManagementError: If service start fails
            BinaryValidationError: If new binaries invalid
        """
        pass


class IServiceManager(ABC):
    """
    Interface for managing systemd services during upgrades.
    
    Used by both web/leader and workers.
    """
    
    @abstractmethod
    def stop_service(self, service_name: str, timeout_seconds: int = 30) -> None:
        """
        Stop systemd service gracefully.
        
        Args:
            service_name: Service name (e.g., "concourse-worker.service")
            timeout_seconds: Maximum time to wait for stop
        
        Raises:
            ServiceManagementError: If stop fails or times out
        """
        pass
    
    @abstractmethod
    def start_service(self, service_name: str, timeout_seconds: int = 30) -> None:
        """
        Start systemd service.
        
        Args:
            service_name: Service name (e.g., "concourse-worker.service")
            timeout_seconds: Maximum time to wait for start
        
        Raises:
            ServiceManagementError: If start fails or times out
        """
        pass
    
    @abstractmethod
    def restart_service(self, service_name: str, timeout_seconds: int = 30) -> None:
        """
        Restart systemd service.
        
        Args:
            service_name: Service name (e.g., "concourse-server.service")
            timeout_seconds: Maximum time to wait for restart
        
        Raises:
            ServiceManagementError: If restart fails or times out
        """
        pass
    
    @abstractmethod
    def is_service_active(self, service_name: str) -> bool:
        """
        Check if service is currently active.
        
        Args:
            service_name: Service name to check
        
        Returns:
            True if service active, False otherwise
        """
        pass


class IRelationDataAccessor(ABC):
    """
    Interface for accessing peer relation data.
    
    Abstracts Juju relation data operations for testability.
    """
    
    @abstractmethod
    def set_unit_data(self, key: str, value: str) -> None:
        """
        Set data on current unit's relation data.
        
        Args:
            key: Relation data key
            value: Relation data value (must be string)
        """
        pass
    
    @abstractmethod
    def get_unit_data(self, unit_name: str, key: str) -> Optional[str]:
        """
        Get data from specific unit's relation data.
        
        Args:
            unit_name: Unit to read from (e.g., "concourse-ci/0")
            key: Relation data key
        
        Returns:
            Value if exists, None otherwise
        """
        pass
    
    @abstractmethod
    def get_all_units(self) -> list[str]:
        """
        Get list of all units in peer relation.
        
        Returns:
            List of unit names (e.g., ["concourse-ci/0", "concourse-ci/1"])
        """
        pass
    
    @abstractmethod
    def set_application_data(self, key: str, value: str) -> None:
        """
        Set application-level relation data (leader only).
        
        Args:
            key: Relation data key
            value: Relation data value
        
        Raises:
            PermissionError: If caller is not leader
        """
        pass
    
    @abstractmethod
    def get_application_data(self, key: str) -> Optional[str]:
        """
        Get application-level relation data.
        
        Args:
            key: Relation data key
        
        Returns:
            Value if exists, None otherwise
        """
        pass


# Protocol guarantees:
#
# 1. Only web/leader may call initiate_upgrade(), wait_for_workers_ready(),
#    mark_download_phase(), complete_upgrade(), reset_upgrade_state()
# 2. Only workers may call handle_prepare_signal(), handle_complete_signal()
# 3. All units may call get_upgrade_state() (read-only)
# 4. Phase transitions are atomic: IDLE → PREPARE → DOWNLOADING → COMPLETE → IDLE
# 5. Workers MUST stop services before acknowledging PREPARE
# 6. Web/leader MUST wait for acknowledgments before downloading
# 7. All timestamps are UTC timezone-aware
# 8. All relation data values are strings (Juju constraint)
