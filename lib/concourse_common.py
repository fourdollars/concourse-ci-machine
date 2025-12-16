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
    configured = config.get("concourse-version")
    if configured:
        return configured
    # Fetch latest version from GitHub
    from concourse_installer import get_latest_concourse_version

    return get_latest_concourse_version()
