#!/usr/bin/env python3
"""
Concourse Installer Library - Handles downloading and installing Concourse
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from ops.model import MaintenanceStatus

from concourse_common import CONCOURSE_INSTALL_DIR, CONCOURSE_BIN

logger = logging.getLogger(__name__)


def get_latest_concourse_version():
    """Fetch the latest Concourse version from GitHub releases"""
    import urllib.request

    try:
        url = "https://api.github.com/repos/concourse/concourse/releases/latest"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            version = data["tag_name"].lstrip("v")
            logger.info(f"Latest Concourse version: {version}")
            return version
    except Exception as e:
        logger.error(f"Failed to fetch latest version from GitHub: {e}")
        raise RuntimeError(f"Cannot determine latest Concourse version: {e}")


def download_and_install_concourse(charm, version: str):
    """Download and install Concourse binaries"""
    import urllib.request
    import tarfile

    url = f"https://github.com/concourse/concourse/releases/download/v{version}/concourse-{version}-linux-amd64.tgz"
    logger.info(f"Downloading Concourse CI {version} from {url}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_file = Path(tmpdir) / "concourse.tar.gz"

            # Download with progress tracking
            last_pct = [0]  # Use list to allow mutation in nested function

            def download_progress(block_num, block_size, total_size):
                downloaded = block_num * block_size
                if total_size > 0:
                    pct = min(100, int(downloaded * 100 / total_size))
                    # Only update when percentage actually changes
                    if pct != last_pct[0]:
                        charm.unit.status = MaintenanceStatus(
                            f"Downloading Concourse CI {version}... {pct}%"
                        )
                        logger.debug(f"Download progress: {pct}%")
                        last_pct[0] = pct

            try:
                urllib.request.urlretrieve(url, tar_file, download_progress)
            except Exception as e:
                logger.error(f"Failed to download from {url}: {e}")
                raise

            # Verify file exists and has content
            if not tar_file.exists() or tar_file.stat().st_size == 0:
                raise RuntimeError(f"Downloaded file is empty or missing: {tar_file}")

            # Extract to a temporary directory first to avoid "text file busy" and symlink issues
            charm.unit.status = MaintenanceStatus(f"Extracting Concourse {version}...")
            try:
                import shutil
                extract_path = Path(tmpdir) / "extract"
                extract_path.mkdir()
                
                with tarfile.open(tar_file, "r:gz") as tar:
                    tar.extractall(path=extract_path)
                
                # The tarball contains a 'concourse/' top-level directory
                src_dir = extract_path / "concourse"
                if not src_dir.exists():
                    # Fallback if top-level dir is different or missing
                    first_dir = next(extract_path.iterdir(), None)
                    if first_dir and first_dir.is_dir():
                        src_dir = first_dir
                    else:
                        src_dir = extract_path
                
                # Move files to CONCOURSE_INSTALL_DIR
                logger.info(f"Moving files from {src_dir} to {CONCOURSE_INSTALL_DIR}")
                for item in src_dir.iterdir():
                    dest = Path(CONCOURSE_INSTALL_DIR) / item.name
                    # If destination exists, remove it first to avoid issues with symlinks or busy files
                    if dest.exists():
                        if dest.is_dir() and not dest.is_symlink():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    shutil.move(str(item), str(dest))
            except (tarfile.TarError, OSError) as e:
                logger.error(f"Failed to extract or move files: {e}")
                raise

            logger.info(f"Concourse {version} installed successfully")
            return version
    except Exception as e:
        logger.error(f"Concourse download/install failed: {e}")
        raise


def verify_installation() -> bool:
    """Verify Concourse is installed correctly"""
    return Path(CONCOURSE_BIN).exists()
