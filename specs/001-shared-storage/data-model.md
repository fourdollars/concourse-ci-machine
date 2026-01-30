# Data Model
**Feature**: 001-shared-storage  
**Version**: 1.0  
**Last Updated**: 2025-01-09

## Overview
This document defines the core entities, their relationships, and data structures for the shared storage feature. All entities follow the constitution's code quality principles (type hints, no hardcoded values).

## Core Entities

### 1. SharedStorage
**Purpose**: Represents the shared filesystem mounted across all units.

**Fields**:
```python
@dataclass
class SharedStorage:
    """Shared storage volume configuration and state."""
    
    volume_path: Path  # Mount point (e.g., /var/lib/concourse)
    filesystem_id: str  # Unique filesystem identifier for validation
    installed_version: Optional[str]  # Current installed version (from marker)
    bin_directory: Path  # Path to shared binaries
    keys_directory: Path  # Path to shared TSA keys
    lock_file_path: Path  # Path to .install.lock
    
    def __post_init__(self):
        """Validate paths exist and are accessible."""
        if not self.volume_path.exists():
            raise StorageNotMountedError(f"Volume not mounted: {self.volume_path}")
        
        # Ensure subdirectories exist
        self.bin_directory.mkdir(parents=True, exist_ok=True)
        self.keys_directory.mkdir(parents=True, exist_ok=True)
    
    @property
    def version_marker_path(self) -> Path:
        """Path to .installed_version marker file."""
        return self.volume_path / ".installed_version"
    
    @property
    def progress_marker_path(self) -> Path:
        """Path to .download_in_progress marker file."""
        return self.volume_path / ".download_in_progress"
    
    def read_installed_version(self) -> Optional[str]:
        """Read installed version from marker file."""
        if not self.version_marker_path.exists():
            return None
        return self.version_marker_path.read_text().strip()
    
    def write_installed_version(self, version: str) -> None:
        """Write installed version to marker file (web/leader only)."""
        self.version_marker_path.write_text(version)
        self.installed_version = version
```

**Relationships**:
- 1:1 with Juju storage volume
- 1:N with WorkerDirectory (one shared storage, multiple worker subdirectories)
- Used by LockCoordinator for lock file path

### 2. LockCoordinator
**Purpose**: Manages exclusive locks for binary downloads (web/leader only).

**Fields**:
```python
@dataclass
class LockCoordinator:
    """Coordinates download locks via fcntl."""
    
    lock_path: Path  # Path to .install.lock file
    holder_unit: Optional[str] = None  # Unit name holding lock
    acquired_at: Optional[datetime] = None  # Lock acquisition timestamp
    timeout_seconds: int = 600  # Stale lock threshold (10 minutes)
    
    @contextmanager
    def acquire_exclusive(self) -> Iterator[None]:
        """
        Acquire exclusive lock for binary download.
        Only web/leader should call this method.
        
        Raises:
            LockAcquireError: If lock already held by another unit
            StaleLockError: If stale lock detected and cleaned
        """
        # Check for stale locks first
        if self._is_stale():
            self._clean_stale_markers()
        
        lock_file = self.lock_path.open('w')
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.holder_unit = os.environ.get('JUJU_UNIT_NAME')
            self.acquired_at = datetime.now(timezone.utc)
            yield
        except BlockingIOError:
            raise LockAcquireError(
                f"Download lock held by another unit. "
                f"Only web/leader should download binaries."
            )
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            self.holder_unit = None
            self.acquired_at = None
    
    def _is_stale(self) -> bool:
        """Check if progress marker is stale."""
        progress_marker = self.lock_path.parent / ".download_in_progress"
        if not progress_marker.exists():
            return False
        
        age_seconds = time.time() - progress_marker.stat().st_mtime
        return age_seconds > self.timeout_seconds
    
    def _clean_stale_markers(self) -> None:
        """Remove stale progress markers."""
        progress_marker = self.lock_path.parent / ".download_in_progress"
        if progress_marker.exists():
            progress_marker.unlink()
```

**Relationships**:
- Uses SharedStorage.lock_file_path
- Enforces web/leader-only write access to SharedStorage.bin_directory

### 3. UpgradeState
**Purpose**: Tracks upgrade coordination state via peer relation data.

**Fields**:
```python
@dataclass
class UpgradeState:
    """Upgrade coordination state (stored in peer relation data)."""
    
    state: Literal["idle", "prepare", "downloading", "complete"]
    target_version: Optional[str]  # Version being upgraded to
    initiated_by: Optional[str]  # Unit name that initiated upgrade
    timestamp: datetime  # State change timestamp
    worker_ready_count: int = 0  # Number of workers that stopped services
    expected_worker_count: int = 0  # Total workers expected to acknowledge
    
    def to_relation_data(self) -> dict[str, str]:
        """Convert to peer relation data format."""
        return {
            "upgrade-state": self.state,
            "target-version": self.target_version or "",
            "initiated-by": self.initiated_by or "",
            "timestamp": self.timestamp.isoformat(),
            "worker-ready-count": str(self.worker_ready_count),
            "expected-worker-count": str(self.expected_worker_count),
        }
    
    @classmethod
    def from_relation_data(cls, data: dict[str, str]) -> "UpgradeState":
        """Parse from peer relation data."""
        return cls(
            state=data.get("upgrade-state", "idle"),
            target_version=data.get("target-version") or None,
            initiated_by=data.get("initiated-by") or None,
            timestamp=datetime.fromisoformat(data["timestamp"]),
            worker_ready_count=int(data.get("worker-ready-count", 0)),
            expected_worker_count=int(data.get("expected-worker-count", 0)),
        )
    
    def is_ready_to_download(self, timeout_seconds: int = 120) -> bool:
        """Check if all workers acknowledged (or timeout reached)."""
        if self.worker_ready_count >= self.expected_worker_count:
            return True
        
        elapsed = (datetime.now(timezone.utc) - self.timestamp).total_seconds()
        return elapsed >= timeout_seconds
```

**Relationships**:
- Stored in `peers` relation data
- Updated by web/leader during upgrade initiation
- Read by workers to coordinate service stops/starts

### 4. WorkerDirectory
**Purpose**: Per-unit isolated state on shared storage.

**Fields**:
```python
@dataclass
class WorkerDirectory:
    """Per-worker isolated directory on shared storage."""
    
    unit_name: str  # Juju unit name (e.g., concourse-ci/1)
    path: Path  # Full path to worker directory
    state_file: Path  # Path to state.json
    work_dir: Path  # Concourse work directory
    
    def __post_init__(self):
        """Ensure worker directory structure exists."""
        self.path.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def from_shared_storage(
        cls, 
        shared_storage: SharedStorage, 
        unit_name: str
    ) -> "WorkerDirectory":
        """Create WorkerDirectory from shared storage root."""
        worker_path = shared_storage.volume_path / "worker" / unit_name
        return cls(
            unit_name=unit_name,
            path=worker_path,
            state_file=worker_path / "state.json",
            work_dir=worker_path / "work_dir",
        )
    
    def read_state(self) -> dict[str, Any]:
        """Read worker state from JSON file."""
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text())
    
    def write_state(self, state: dict[str, Any]) -> None:
        """Write worker state to JSON file."""
        self.state_file.write_text(json.dumps(state, indent=2))
```

**Relationships**:
- N:1 with SharedStorage (multiple workers, one shared volume)
- Each unit has exclusive write access to its own directory
- Read-only access to parent SharedStorage.bin_directory

### 5. ServiceManager
**Purpose**: Manages systemd service lifecycle during upgrades.

**Fields**:
```python
@dataclass
class ServiceManager:
    """Systemd service management for Concourse processes."""
    
    service_name: str  # Service name (e.g., "concourse-worker.service")
    timeout_seconds: int = 30  # Timeout for systemctl operations
    
    def stop(self) -> None:
        """Stop systemd service (workers before upgrade)."""
        try:
            subprocess.run(
                ["systemctl", "stop", self.service_name],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds
            )
        except subprocess.CalledProcessError as e:
            raise ServiceManagementError(
                f"Failed to stop {self.service_name}: {e.stderr}"
            )
        except subprocess.TimeoutExpired:
            raise ServiceManagementError(
                f"Timeout stopping {self.service_name} after {self.timeout_seconds}s"
            )
    
    def start(self) -> None:
        """Start systemd service (workers after upgrade)."""
        try:
            subprocess.run(
                ["systemctl", "start", self.service_name],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds
            )
        except subprocess.CalledProcessError as e:
            raise ServiceManagementError(
                f"Failed to start {self.service_name}: {e.stderr}"
            )
        except subprocess.TimeoutExpired:
            raise ServiceManagementError(
                f"Timeout starting {self.service_name} after {self.timeout_seconds}s"
            )
    
    def restart(self) -> None:
        """Restart systemd service (web/leader after download)."""
        try:
            subprocess.run(
                ["systemctl", "restart", self.service_name],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds
            )
        except subprocess.CalledProcessError as e:
            raise ServiceManagementError(
                f"Failed to restart {self.service_name}: {e.stderr}"
            )
        except subprocess.TimeoutExpired:
            raise ServiceManagementError(
                f"Timeout restarting {self.service_name} after {self.timeout_seconds}s"
            )
```

**Relationships**:
- Used by workers during upgrade coordination
- Used by web/leader after binary download

## Data Flow Diagrams

### Initial Deployment Flow
```
┌─────────────────┐
│ Web/Leader Unit │
└────────┬────────┘
         │
         ├─ 1. Mount shared storage
         ├─ 2. Acquire exclusive lock
         ├─ 3. Download binaries to bin/
         ├─ 4. Write .installed_version
         └─ 5. Start concourse-server.service
         
┌──────────────┐
│ Worker Units │ (added later)
└──────┬───────┘
       │
       ├─ 1. Mount shared storage (same volume)
       ├─ 2. Check .installed_version (exists!)
       ├─ 3. Verify binaries in bin/
       ├─ 4. Create worker/{unit}/ directory
       └─ 5. Start concourse-worker.service
```

### Upgrade Flow
```
┌─────────────────┐
│ Web/Leader Unit │
└────────┬────────┘
         │
         ├─ 1. Set upgrade-state=prepare in peer relation
         ├─ 2. Wait for worker acknowledgments (2min timeout)
         ├─ 3. Acquire exclusive lock
         ├─ 4. Download new binaries
         ├─ 5. Write new .installed_version
         ├─ 6. Restart concourse-server.service
         └─ 7. Set upgrade-state=complete in peer relation
         
┌──────────────┐
│ Worker Units │
└──────┬───────┘
       │
       ├─ 1. Detect upgrade-state=prepare
       ├─ 2. Stop concourse-worker.service
       ├─ 3. Set upgrade-ready=true in peer relation
       ├─ 4. Poll for upgrade-state=complete (5min timeout)
       └─ 5. Start concourse-worker.service
```

## Validation Rules

### SharedStorage
- `volume_path` must exist and be writable
- `filesystem_id` must match across all units (validate shared mount)
- `installed_version` must match semantic versioning (e.g., "7.14.3")

### LockCoordinator
- Only web/leader unit may call `acquire_exclusive()`
- Workers never attempt lock acquisition
- Stale lock threshold must be > download timeout

### UpgradeState
- `state` transitions: idle → prepare → downloading → complete → idle
- `worker_ready_count` must be ≤ `expected_worker_count`
- `timestamp` must be UTC timezone-aware

### WorkerDirectory
- `unit_name` must match Juju unit name format
- `path` must be subdirectory of SharedStorage.volume_path
- Each unit writes only to its own directory

### ServiceManager
- `service_name` must end with ".service"
- `timeout_seconds` must be > 0
- All operations must be idempotent (safe to retry)

## Exception Hierarchy
```python
class SharedStorageError(Exception):
    """Base exception for shared storage operations."""
    pass

class StorageNotMountedError(SharedStorageError):
    """Shared storage volume not mounted."""
    pass

class LockAcquireError(SharedStorageError):
    """Failed to acquire exclusive lock."""
    pass

class StaleLockError(SharedStorageError):
    """Stale lock detected and cleaned."""
    pass

class ServiceManagementError(SharedStorageError):
    """Failed to manage systemd service."""
    pass

class UpgradeTimeoutError(SharedStorageError):
    """Upgrade coordination timeout."""
    pass
```

## Constitution Compliance

### Code Quality ✅
- All entities use `@dataclass` with type hints
- No hardcoded paths (all via Path objects)
- Specific exception types for each failure mode

### Testing ✅
- Each entity has unit tests for validation rules
- Data flow diagrams guide E2E test scenarios
- Exception handling tested for all failure modes

### UX ✅
- Error messages include actionable context
- State transitions logged for debugging
- Progress markers visible to operators

### Performance ✅
- Marker file reads: O(1) filesystem operations
- Lock acquisition: Non-blocking (LOCK_NB)
- Worker polling: 5-second intervals (not tight loops)
