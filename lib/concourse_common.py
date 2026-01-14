#!/usr/bin/env python3
"""
Concourse Common Library - Shared constants and utilities
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Installation and configuration paths
# Use /var/lib/concourse for everything - no /opt/concourse
CONCOURSE_DATA_DIR = "/var/lib/concourse"
CONCOURSE_WORKER_DATA_DIR = "/var/lib/concourse-worker"
CONCOURSE_CONFIG_FILE = f"{CONCOURSE_DATA_DIR}/config.env"
CONCOURSE_WORKER_CONFIG_FILE = f"{CONCOURSE_WORKER_DATA_DIR}/worker-config.env"
CONCOURSE_BIN = f"{CONCOURSE_DATA_DIR}/bin/concourse"
SYSTEMD_SERVICE_DIR = "/etc/systemd/system"
KEYS_DIR = f"{CONCOURSE_DATA_DIR}/keys"
WORKER_KEYS_DIR = f"{CONCOURSE_WORKER_DATA_DIR}/keys"


def ensure_directories(skip_shared_storage: bool = False):
    """Ensure required directories exist.
    
    Args:
        skip_shared_storage: If True, create worker directories instead of web directories
    """
    if skip_shared_storage:
        # Worker: create writable worker directories, skip shared storage
        dirs = [CONCOURSE_WORKER_DATA_DIR, WORKER_KEYS_DIR]
    else:
        # Web: create shared storage directories
        dirs = [CONCOURSE_DATA_DIR, KEYS_DIR]
    
    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        # Skip chmod on shared storage if it's readonly (workers accessing shared mount)
        if skip_shared_storage and dir_path in [CONCOURSE_DATA_DIR, KEYS_DIR]:
            logger.debug(f"Skipping chmod on readonly directory: {dir_path}")
            continue
        try:
            os.chmod(dir_path, 0o755)
        except OSError as e:
            # If chmod fails on shared storage, it's likely readonly - that's okay
            logger.debug(f"Cannot chmod {dir_path}: {e}")
    logger.info(f"Ensured directories exist: {', '.join(dirs)}")


def generate_keys():
    """Generate Concourse TSA and session signing keys with correct ownership"""
    import pwd
    import grp
    
    keys_dir = Path(KEYS_DIR)
    
    # Ensure keys directory exists
    keys_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured keys directory exists: {keys_dir}")
    
    tsa_host_key = keys_dir / "tsa_host_key"
    session_signing_key = keys_dir / "session_signing_key"
    worker_key = keys_dir / "worker_key"
    
    # Get concourse user UID/GID
    try:
        concourse_user = pwd.getpwnam("concourse")
        uid = concourse_user.pw_uid
        gid = concourse_user.pw_gid
    except KeyError:
        logger.warning("concourse user not found, keys will be owned by root")
        uid = gid = 0

    # Generate TSA host key if it doesn't exist
    if not tsa_host_key.exists():
        logger.info("Generating TSA host key...")
        subprocess.run(
            [CONCOURSE_BIN, "generate-key", "-t", "ssh", "-f", str(tsa_host_key)],
            check=True,
            capture_output=True,
        )
        os.chmod(tsa_host_key, 0o600)
        os.chmod(f"{tsa_host_key}.pub", 0o644)
        os.chown(tsa_host_key, uid, gid)
        os.chown(f"{tsa_host_key}.pub", uid, gid)
        logger.info("TSA host key generated")

    # Generate session signing key if it doesn't exist
    if not session_signing_key.exists():
        logger.info("Generating session signing key...")
        subprocess.run(
            [
                CONCOURSE_BIN,
                "generate-key",
                "-t",
                "rsa",
                "-f",
                str(session_signing_key),
            ],
            check=True,
            capture_output=True,
        )
        os.chmod(session_signing_key, 0o600)
        os.chown(session_signing_key, uid, gid)
        logger.info("Session signing key generated")

    # Generate worker key if it doesn't exist
    if not worker_key.exists():
        logger.info("Generating worker key...")
        subprocess.run(
            [CONCOURSE_BIN, "generate-key", "-t", "ssh", "-f", str(worker_key)],
            check=True,
            capture_output=True,
        )
        os.chmod(worker_key, 0o600)
        os.chmod(f"{worker_key}.pub", 0o644)
        os.chown(worker_key, uid, gid)
        os.chown(f"{worker_key}.pub", uid, gid)
        logger.info("Worker key generated")

    # Setup authorized_worker_keys
    authorized_keys = keys_dir / "authorized_worker_keys"
    if not authorized_keys.exists():
        with open(f"{worker_key}.pub", "r") as f:
            worker_pub = f.read()
        authorized_keys.write_text(worker_pub)
        os.chmod(authorized_keys, 0o644)
        os.chown(authorized_keys, uid, gid)
        os.chmod(authorized_keys, 0o644)
        logger.info("Authorized worker keys configured")

    # Change ownership to concourse user
    try:
        subprocess.run(
            ["chown", "-R", "concourse:concourse", str(keys_dir)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        logger.warning(
            "Could not change key ownership (concourse user may not exist yet)"
        )


def create_concourse_user():
    """Create concourse system user"""
    try:
        result = subprocess.run(
            ["id", "concourse"],
            capture_output=True,
        )
        if result.returncode != 0:
            subprocess.run(
                [
                    "useradd",
                    "-r",
                    "-s",
                    "/bin/false",
                    "-d",
                    CONCOURSE_DATA_DIR,
                    "concourse",
                ],
                capture_output=True,
                check=True,
            )
            logger.info("Concourse user created")
        else:
            logger.info("Concourse user already exists")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create concourse user: {e}")
        raise


def get_concourse_version(config) -> str:
    """Get configured or latest Concourse version"""
    configured = config.get("version")
    if configured:
        return configured
    # Fetch latest version from GitHub
    from concourse_installer import get_latest_concourse_version

    return get_latest_concourse_version()


def detect_nvidia_gpus():
    """
    Detect NVIDIA GPUs on the system
    
    Returns:
        dict with keys: count, devices (list of dicts with index, name, driver)
        or None if no GPUs found or nvidia-smi unavailable
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
        )
        
        devices = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    devices.append({
                        "index": int(parts[0]),
                        "name": parts[1],
                        "driver": parts[2] if len(parts) > 2 else "unknown"
                    })
        
        if devices:
            logger.info(f"Detected {len(devices)} NVIDIA GPU(s)")
            return {"count": len(devices), "devices": devices}
        else:
            logger.info("No NVIDIA GPUs detected")
            return None
            
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Failed to detect NVIDIA GPUs: {e}")
        return None


def verify_nvidia_container_runtime():
    """
    Verify NVIDIA container runtime is available
    
    Returns:
        bool: True if nvidia-container-runtime is available
    """
    try:
        result = subprocess.run(
            ["which", "nvidia-container-runtime"],
            capture_output=True,
            check=True,
        )
        logger.info("NVIDIA container runtime is available")
        return True
    except subprocess.CalledProcessError:
        logger.warning("NVIDIA container runtime not found")
        return False


def get_storage_path(storage_name: str = "concourse-data") -> Optional[Path]:
    """Get storage mount path from saved state.
    
    The storage location is saved during the storage-attached hook
    and retrieved here for use in other hooks.
    
    Args:
        storage_name: Name of storage (for logging only)
    
    Returns:
        Path to storage mount point, or None if storage not attached
    """
    try:
        # Read storage location from state file saved by storage-attached hook
        # Pattern: /var/lib/juju/agents/unit-{app-name}-{unit-num}/.storage-location
        import os
        unit_name = os.environ.get("JUJU_UNIT_NAME", "")
        if not unit_name:
            logger.warning("JUJU_UNIT_NAME not set, cannot get storage path")
            return None
            
        state_file = Path("/var/lib/juju/agents") / f"unit-{unit_name}" / ".storage-location"
        if not state_file.exists():
            logger.info(f"Storage '{storage_name}' not attached (no state file)")
            return None
            
        location = state_file.read_text().strip()
        if location:
            path = Path(location)
            logger.info(f"Storage '{storage_name}' mounted at: {path}")
            return path
        else:
            logger.warning(f"Storage '{storage_name}' location is empty")
            return None
    except Exception as e:
        logger.error(f"Failed to get storage location: {e}")
        return None


def get_filesystem_id(path: Path) -> str:
    """Get unique filesystem identifier for a given path.
    
    Uses stat to get the filesystem ID, which ensures all units
    are accessing the same filesystem when using shared storage.
    
    Args:
        path: Path to check filesystem ID for
    
    Returns:
        Filesystem ID as string
    
    Raises:
        RuntimeError: If unable to get filesystem ID
    """
    try:
        result = subprocess.run(
            ["stat", "-f", "-c", "%i", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        fs_id = result.stdout.strip()
        logger.info(f"Filesystem ID for {path}: {fs_id}")
        return fs_id
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get filesystem ID: {e.stderr}")
        raise RuntimeError(f"Cannot get filesystem ID for {path}: {e.stderr}")


def get_storage_logger(unit_name: str) -> logging.Logger:
    """Get logger with unit name prefix for storage coordination.
    
    Creates a logger that prefixes all messages with the unit name
    for easier debugging in multi-unit deployments.
    
    Args:
        unit_name: Juju unit name (e.g., "concourse-ci/1")
    
    Returns:
        Logger instance with custom formatter
    
    Example:
        logger = get_storage_logger("concourse-ci/1")
        logger.info("Waiting for binaries...")
        # Output: [concourse-ci/1] Waiting for binaries...
    """
    storage_logger = logging.getLogger(f"concourse-ci.storage.{unit_name}")
    
    # Add custom handler with unit prefix if not already present
    if not storage_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            f"[{unit_name}] %(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        storage_logger.addHandler(handler)
        storage_logger.setLevel(logging.INFO)
    
    return storage_logger


def log_concurrent_operation(
    unit_name: str, 
    operation: str, 
    lock_held: bool = False, 
    details: str = ""
) -> None:
    """Log concurrent storage operation details (T061).
    
    Args:
        unit_name: Current unit name
        operation: Operation name
        lock_held: Whether lock is currently held by this unit
        details: Additional operation details
    """
    logger = get_storage_logger(unit_name)
    status = "LOCKED" if lock_held else "UNLOCKED"
    logger.info(f"Concurrent Op: {operation} [{status}] - {details}")


def get_storage_stats() -> dict:
    """Get storage statistics for monitoring (T076).
    
    Returns:
        dict with disk_usage_bytes, binary_count, unit_count
    """
    stats = {
        "disk_usage_bytes": 0,
        "binary_count": 0,
        "worker_count": 0,
        "shared_storage": False
    }
    
    try:
        data_dir = Path(CONCOURSE_DATA_DIR)
        if not data_dir.exists():
            return stats
            
        # Disk usage
        total_size = 0
        for p in data_dir.rglob('*'):
            if p.is_file() and not p.is_symlink():
                try:
                    total_size += p.stat().st_size
                except OSError:
                    pass
        stats["disk_usage_bytes"] = total_size
        
        # Binary count
        bin_dir = data_dir / "bin"
        if bin_dir.exists():
            stats["binary_count"] = sum(1 for _ in bin_dir.iterdir())
            
        # Worker count (based on directories)
        worker_dir = data_dir / "worker"
        if worker_dir.exists():
            stats["worker_count"] = sum(1 for p in worker_dir.iterdir() if p.is_dir())
            
        # Shared storage check
        if (data_dir / ".lxc_shared_storage").exists():
            stats["shared_storage"] = True
            
        return stats
    except Exception as e:
        logger.warning(f"Failed to collect storage stats: {e}")
        return stats
