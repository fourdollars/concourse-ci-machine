#!/usr/bin/env python3
"""
Concourse Installer Library - Handles downloading and installing Concourse
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
from ops.model import MaintenanceStatus

from concourse_common import CONCOURSE_BIN, CONCOURSE_DATA_DIR

CONCOURSE_INSTALL_DIR = f"{CONCOURSE_DATA_DIR}/bin"

logger = logging.getLogger(__name__)

# Import storage coordinator (may not be available in all contexts)
try:
    from storage_coordinator import LockAcquireError

    HAS_STORAGE_COORDINATOR = True
except ImportError:
    HAS_STORAGE_COORDINATOR = False

    # Define dummy exception if module missing so except block doesn't fail
    class LockAcquireError(Exception):
        pass

    logger.warning("storage_coordinator not available, shared storage disabled")


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


def detect_existing_binaries(
    storage_coordinator: Optional[object] = None, expected_version: Optional[str] = None
) -> Optional[str]:
    """Check if binaries already exist on shared storage (T017).

    Args:
        storage_coordinator: StorageCoordinator instance (optional)
        expected_version: Expected version to check for (optional)

    Returns:
        Installed version if binaries exist and are valid, None otherwise
    """
    if not storage_coordinator:
        # No shared storage, check local installation
        if Path(CONCOURSE_BIN).exists():
            logger.info("Local Concourse binary found")
            return "unknown"  # Version unknown without shared storage
        return None

    try:
        installed_version = storage_coordinator.get_installed_version()
        if installed_version:
            logger.info(f"Found existing binaries: v{installed_version}")

            # Verify if expected version matches
            if expected_version and installed_version != expected_version:
                logger.warning(
                    f"Version mismatch: expected {expected_version}, "
                    f"found {installed_version}"
                )
                return None

            # Verify binaries are valid
            if verify_binaries(storage_coordinator, installed_version):
                logger.info(f"Existing binaries v{installed_version} are valid")
                return installed_version
            else:
                logger.warning(f"Existing binaries v{installed_version} are invalid")
                return None
        else:
            logger.info("No existing binaries found on shared storage")
            return None
    except Exception as e:
        logger.error(f"Error detecting existing binaries: {e}")
        return None


def verify_binaries(
    storage_coordinator: Optional[object] = None, version: Optional[str] = None
) -> bool:
    """Verify that binaries are valid and executable (T018).

    Args:
        storage_coordinator: StorageCoordinator instance (optional)
        version: Version to verify (optional, uses installed version if not provided)

    Returns:
        True if binaries are valid, False otherwise
    """
    if not storage_coordinator:
        # No shared storage, check local installation
        binary_path = Path(CONCOURSE_BIN)
        if not binary_path.exists():
            logger.warning(f"Binary not found: {binary_path}")
            return False

        if not os.access(binary_path, os.X_OK):
            logger.warning(f"Binary not executable: {binary_path}")
            return False

        logger.info("Local binary is valid")
        return True

    try:
        # Use storage coordinator's verify method
        if version is None:
            version = storage_coordinator.get_installed_version()
            if not version:
                logger.warning("No installed version found")
                return False

        is_valid = storage_coordinator.verify_binaries(version)
        if is_valid:
            logger.info(f"Binaries v{version} verified successfully")
        else:
            logger.warning(f"Binaries v{version} failed verification")

        return is_valid
    except Exception as e:
        logger.error(f"Error verifying binaries: {e}")
        return False


def download_and_install_concourse_with_storage(
    charm, version: str, storage_coordinator: Optional[object] = None
):
    """Download and install Concourse binaries with shared storage support (T019-T021).

    This function extends the original download_and_install_concourse with:
    - Lock acquisition for exclusive download (web/leader only)
    - Progress marker creation (.download_in_progress)
    - Version marker writing after successful download
    - Worker waiting support
    - LXC shared storage marker detection

    Args:
        charm: Charm instance for status updates
        version: Version to download (e.g., "7.14.3")
        storage_coordinator: StorageCoordinator instance (optional)

    Returns:
        Downloaded version string

    Raises:
        RuntimeError: If download fails or lock cannot be acquired
    """
    # If no storage coordinator, fall back to original function
    if not storage_coordinator:
        logger.info("No storage coordinator, using local installation")
        return download_and_install_concourse(charm, version)

    # Check for LXC shared storage marker - both web and worker must wait
    import time

    if hasattr(storage_coordinator.storage, "is_lxc_shared_storage"):
        if not storage_coordinator.storage.is_lxc_shared_storage():
            logger.info("Waiting for LXC shared storage marker...")
            charm.unit.status = MaintenanceStatus(
                "Waiting for shared storage to be configured..."
            )

            # Wait up to 5 minutes for the LXC marker
            for i in range(60):  # 60 * 5s = 5 minutes
                if storage_coordinator.storage.lxc_shared_marker_path.exists():
                    logger.info("LXC shared storage marker found!")
                    break
                time.sleep(5)
            else:
                logger.warning(
                    "LXC shared marker not found after 5 minutes, proceeding anyway"
                )
                # Fall back to local installation if no LXC marker appears
                return download_and_install_concourse(charm, version)

    # Check if binaries already exist
    existing_version = detect_existing_binaries(storage_coordinator, version)
    if existing_version == version:
        logger.info(f"Binaries v{version} already installed, skipping download")
        charm.unit.status = MaintenanceStatus(f"Binaries v{version} already available")
        return version

    # Web/leader downloads, workers wait
    if not storage_coordinator.is_web_leader():
        logger.info("Worker unit: waiting for web/leader to download binaries")
        charm.unit.status = MaintenanceStatus(f"Waiting for binaries v{version}...")

        # Wait for binaries indefinitely (workers should wait for leader upgrade)
        success = storage_coordinator.wait_for_binaries(
            expected_version=version,
            timeout_seconds=0,  # Infinite wait
            poll_interval_seconds=5,
        )

        if not success:
            raise RuntimeError(
                f"Timeout waiting for binaries v{version} from web/leader"
            )

        logger.info(f"Binaries v{version} are now available")
        charm.unit.status = MaintenanceStatus(f"Binaries v{version} ready")
        return version

    # Web/leader: acquire lock and download (T019)
    logger.info("Web/leader unit: acquiring download lock")

    # Retry lock acquisition (T054)
    max_lock_retries = 3
    import time

    for attempt in range(max_lock_retries):
        try:
            with storage_coordinator.acquire_download_lock(timeout_seconds=0):
                logger.info("Download lock acquired, starting download")

                # Create progress marker (T021)
                storage_coordinator.mark_download_started(version)
                charm.unit.status = MaintenanceStatus(
                    f"Downloading Concourse v{version}..."
                )

                try:
                    # Perform actual download
                    _download_and_extract_binaries(
                        charm, version, storage_coordinator.storage.bin_directory
                    )

                    # Write version marker (T020)
                    storage_coordinator.mark_download_complete(version)
                    logger.info(
                        f"Successfully downloaded and marked v{version} as complete"
                    )

                    charm.unit.status = MaintenanceStatus(
                        f"Binaries v{version} installed"
                    )
                    return version

                except Exception:
                    # Clean up progress marker on failure
                    try:
                        bin_dir = storage_coordinator.storage.bin_directory
                        if bin_dir.exists():
                            progress_marker = bin_dir / ".download_in_progress"
                            if progress_marker.exists():
                                progress_marker.unlink()
                    except Exception:
                        pass
                    raise
            break  # Success, exit loop

        except LockAcquireError:
            if attempt < max_lock_retries - 1:
                wait_time = 5 * (attempt + 1)
                logger.warning(
                    f"Lock held by another unit, retrying in {wait_time}s..."
                )
                charm.unit.status = MaintenanceStatus(
                    f"Waiting for download lock... (retry {attempt + 1})"
                )
                time.sleep(wait_time)
            else:
                logger.error("Failed to acquire download lock after retries")
                raise RuntimeError(
                    "Another unit is currently downloading binaries. Please try again later."
                )
        except Exception as e:
            logger.error(f"Failed to download binaries with lock: {e}")
            raise RuntimeError(f"Download failed: {e}")


def _download_and_extract_binaries(charm, version: str, target_dir: Path):
    """Internal function to download and extract Concourse binaries.

    Args:
        charm: Charm instance for status updates
        version: Version to download
        target_dir: Directory to extract binaries to
    """
    import urllib.request
    import tarfile
    import time
    import shutil
    import hashlib

    url = f"https://github.com/concourse/concourse/releases/download/v{version}/concourse-{version}-linux-amd64.tgz"
    sha1_url = f"{url}.sha1"
    logger.info(f"Downloading Concourse CI {version} from {url}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tar_file = Path(tmpdir) / "concourse.tar.gz"
        sha1_file = Path(tmpdir) / "concourse.sha1"

        # Download with progress tracking
        last_pct = [0]
        max_retries = 3
        retry_delay = 5

        def download_progress(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                pct = min(100, int(downloaded * 100 / total_size))
                if pct != last_pct[0]:
                    charm.unit.status = MaintenanceStatus(
                        f"Downloading Concourse CI {version}... {pct}%"
                    )
                    last_pct[0] = pct

        # Retry download on failure
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(
                        f"Retrying download (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(retry_delay * attempt)
                    last_pct[0] = 0

                # Download binary
                start_time = time.time()
                urllib.request.urlretrieve(url, tar_file, download_progress)
                end_time = time.time()

                if not tar_file.exists() or tar_file.stat().st_size == 0:
                    raise RuntimeError(f"Downloaded file is empty: {tar_file}")

                duration = end_time - start_time
                size_mb = tar_file.stat().st_size / (1024 * 1024)
                speed_mbps = size_mb / duration if duration > 0 else 0
                logger.info(
                    f"Download completed: {size_mb:.1f}MB in {duration:.1f}s "
                    f"({speed_mbps:.2f} MB/s)"
                )

                # Download and verify checksum (T058)
                logger.info("Verifying checksum...")
                try:
                    urllib.request.urlretrieve(sha1_url, sha1_file)
                    expected_sha1 = sha1_file.read_text().split()[0].strip()

                    hasher = hashlib.sha1()
                    with open(tar_file, "rb") as f:
                        while chunk := f.read(8192):
                            hasher.update(chunk)
                    actual_sha1 = hasher.hexdigest()

                    if actual_sha1 != expected_sha1:
                        raise RuntimeError(
                            f"Checksum mismatch: expected {expected_sha1}, got {actual_sha1}"
                        )
                    logger.info("Checksum verified successfully")
                except Exception as e:
                    logger.warning(f"Checksum verification failed: {e}")
                    # If we can't verify checksum, should we fail?
                    # T058 implies we should detect corruption.
                    # If fetching sha1 fails, maybe warn? But if mismatch, definitely fail.
                    if "Checksum mismatch" in str(e):
                        raise
                    # For network errors fetching sha1, we might want to retry the whole loop?
                    # Or just warn. Let's fail safe and retry.
                    raise

                break

            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise
                if tar_file.exists():
                    tar_file.unlink()

        # Extract binaries
        charm.unit.status = MaintenanceStatus(f"Extracting Concourse {version}...")
        extract_path = Path(tmpdir) / "extract"
        extract_path.mkdir()

        with tarfile.open(tar_file, "r:gz") as tar:
            tar.extractall(path=extract_path)

        # Find source directory (tarball extracts to concourse/)
        src_dir = extract_path / "concourse"
        if not src_dir.exists():
            first_dir = next(extract_path.iterdir(), None)
            if first_dir and first_dir.is_dir():
                src_dir = first_dir
            else:
                src_dir = extract_path

        # For shared storage, target_dir is /var/lib/concourse/bin
        # but tarball contains concourse/bin/, concourse/resource-types/
        # So we need to move contents of extracted concourse/* to parent of target_dir
        # e.g., move concourse/bin -> /var/lib/concourse/bin
        #       move concourse/resource-types -> /var/lib/concourse/resource-types

        # Get the parent directory (e.g., /var/lib/concourse)
        parent_dir = target_dir.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        # Move files to parent directory
        logger.info(f"Installing binaries from {src_dir} to {parent_dir}")
        for item in src_dir.iterdir():
            dest = parent_dir / item.name
            if dest.exists():
                if dest.is_dir() and not dest.is_symlink():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))

        # Generate checksum for installed binary (T064)
        concourse_bin = parent_dir / "bin" / "concourse"
        if concourse_bin.exists():
            try:
                hasher = hashlib.sha256()
                with open(concourse_bin, "rb") as f:
                    while chunk := f.read(8192):
                        hasher.update(chunk)
                checksum = hasher.hexdigest()
                (parent_dir / "bin" / ".concourse.sha256").write_text(checksum)
                logger.info(f"Generated checksum for installed binary: {checksum}")
            except Exception as e:
                logger.warning(f"Failed to generate binary checksum: {e}")

        logger.info(f"Concourse {version} installed to {target_dir}")


def download_and_install_concourse(charm, version: str):
    """Download and install Concourse binaries"""
    import urllib.request
    import tarfile
    import time
    import subprocess
    import re

    # Idempotency check: if version already installed, skip
    try:
        if Path(CONCOURSE_BIN).exists():
            result = subprocess.run(
                [CONCOURSE_BIN, "-v"], capture_output=True, text=True
            )
            output = (result.stdout or result.stderr or "").strip()
            match = re.search(r"v?(\d+\.\d+\.\d+)", output)
            if match and match.group(1) == version:
                logger.info(f"Concourse {version} already installed, skipping download")
                charm.unit.status = MaintenanceStatus(
                    f"Concourse {version} already installed"
                )
                return version
    except Exception as e:
        logger.debug(f"Failed to check existing version: {e}")
        # Proceed with download if check fails

    # Safety check: Verify /var/lib/concourse is writable if it exists
    # This prevents conflicts when shared storage is mounted but config says "none"
    concourse_dir = Path("/var/lib/concourse")
    if concourse_dir.exists():
        # Check if it's writable by trying to create a test file
        test_file = concourse_dir / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
            logger.info(f"{concourse_dir} is writable, proceeding with download")
        except (PermissionError, OSError) as e:
            error_msg = (
                f"Cannot write to {concourse_dir}. "
                f"If shared storage is mounted, set shared-storage=lxc config. "
                f"Error: {e}"
            )
            logger.error(error_msg)
            from ops.model import BlockedStatus

            charm.unit.status = BlockedStatus("Storage not writable")
            raise RuntimeError(error_msg)

    url = f"https://github.com/concourse/concourse/releases/download/v{version}/concourse-{version}-linux-amd64.tgz"
    logger.info(f"Downloading Concourse CI {version} from {url}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_file = Path(tmpdir) / "concourse.tar.gz"

            # Download with progress tracking and retry logic
            last_pct = [0]  # Use list to allow mutation in nested function
            max_retries = 3
            retry_delay = 5  # seconds

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

            # Retry download on failure
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        logger.info(
                            f"Retrying download (attempt {attempt + 1}/{max_retries})..."
                        )
                        charm.unit.status = MaintenanceStatus(
                            f"Retrying download (attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(retry_delay * attempt)  # Exponential backoff
                        last_pct[0] = 0  # Reset progress

                    urllib.request.urlretrieve(url, tar_file, download_progress)

                    # Verify download completed successfully
                    if not tar_file.exists() or tar_file.stat().st_size == 0:
                        raise RuntimeError(
                            f"Downloaded file is empty or missing: {tar_file}"
                        )

                    logger.info(
                        f"Download completed successfully ({tar_file.stat().st_size} bytes)"
                    )
                    break  # Success, exit retry loop

                except Exception as e:
                    logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                    if attempt == max_retries - 1:
                        # Last attempt failed
                        logger.error(
                            f"Failed to download from {url} after {max_retries} attempts: {e}"
                        )
                        raise
                    # Clean up partial download before retry
                    if tar_file.exists():
                        tar_file.unlink()

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

                # Move files to CONCOURSE_DATA_DIR
                target_base = Path(CONCOURSE_DATA_DIR)
                target_base.mkdir(parents=True, exist_ok=True)

                logger.info(f"Moving files from {src_dir} to {target_base}")
                for item in src_dir.iterdir():
                    dest = target_base / item.name
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
