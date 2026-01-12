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
CONCOURSE_INSTALL_DIR = "/opt/concourse"
CONCOURSE_DATA_DIR = "/var/lib/concourse"
CONCOURSE_CONFIG_FILE = f"{CONCOURSE_DATA_DIR}/config.env"
CONCOURSE_WORKER_CONFIG_FILE = f"{CONCOURSE_DATA_DIR}/worker-config.env"
CONCOURSE_BIN = f"{CONCOURSE_INSTALL_DIR}/bin/concourse"
SYSTEMD_SERVICE_DIR = "/etc/systemd/system"
KEYS_DIR = f"{CONCOURSE_DATA_DIR}/keys"


def ensure_directories():
    """Ensure required directories exist"""
    dirs = [CONCOURSE_INSTALL_DIR, CONCOURSE_DATA_DIR, KEYS_DIR]
    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        os.chmod(dir_path, 0o755)
    logger.info(f"Ensured directories exist: {', '.join(dirs)}")


def generate_keys():
    """Generate Concourse TSA and session signing keys"""
    keys_dir = Path(KEYS_DIR)
    tsa_host_key = keys_dir / "tsa_host_key"
    session_signing_key = keys_dir / "session_signing_key"
    worker_key = keys_dir / "worker_key"

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
        logger.info("Worker key generated")

    # Setup authorized_worker_keys
    authorized_keys = keys_dir / "authorized_worker_keys"
    if not authorized_keys.exists():
        with open(f"{worker_key}.pub", "r") as f:
            worker_pub = f.read()
        authorized_keys.write_text(worker_pub)
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
