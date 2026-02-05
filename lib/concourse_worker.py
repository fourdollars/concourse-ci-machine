#!/usr/bin/env python3
"""
Concourse Worker Helper Library
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from concourse_common import (
    CONCOURSE_BIN,
    CONCOURSE_WORKER_CONFIG_FILE,
    CONCOURSE_DATA_DIR,
    SYSTEMD_SERVICE_DIR,
    KEYS_DIR,
    detect_nvidia_gpus,
    verify_nvidia_container_runtime,
    detect_amd_gpus,
    verify_amd_container_runtime,
    get_filesystem_id,
)

logger = logging.getLogger(__name__)

# Import storage coordinator (may not be available)
try:
    from storage_coordinator import (
        SharedStorage,
        LockCoordinator,
        StorageCoordinator,
        WorkerDirectory,
    )

    HAS_STORAGE_COORDINATOR = True
except ImportError:
    HAS_STORAGE_COORDINATOR = False
    logger.warning("storage_coordinator not available")


class ConcourseWorkerHelper:
    """Helper class for Concourse worker operations"""

    def __init__(self, charm):
        self.charm = charm
        self.model = charm.model
        self.config = charm.model.config
        self.storage_coordinator = (
            None  # Will be initialized if shared storage available
        )
        self.worker_directory = None  # Per-unit worker directory on shared storage
        self.detected_gpu_type = None  # Cache detected GPU type

    def _is_gpu_enabled(self) -> bool:
        """Check if GPU support is enabled via compute-runtime config.

        Returns:
            True if compute-runtime is 'cuda' or 'rocm', False if 'none' or unset
        """
        compute_runtime = self.config.get("compute-runtime", "none")
        return compute_runtime in ["cuda", "rocm"]

    def _detect_gpu_type(self):
        """Detect which GPU type is available on the system

        Returns:
            str: 'cuda', 'rocm', or None if no GPU detected
        """
        if self.detected_gpu_type:
            return self.detected_gpu_type

        compute_runtime_config = self.config.get("compute-runtime", "none")

        # If GPU is disabled, return None immediately
        if compute_runtime_config == "none":
            return None

        if compute_runtime_config == "cuda":
            self.detected_gpu_type = "cuda"
            return "cuda"
        elif compute_runtime_config == "rocm":
            self.detected_gpu_type = "rocm"
            return "rocm"

        # Should not reach here since we removed 'auto' mode
        logger.warning(f"Unknown compute-runtime value: {compute_runtime_config}")
        return None

    def initialize_shared_storage(self) -> Optional[object]:
        """Initialize shared storage for worker unit (T023).

        Returns:
            StorageCoordinator instance if shared storage is available, None otherwise
        """
        if not HAS_STORAGE_COORDINATOR:
            logger.info("Storage coordinator not available, skipping shared storage")
            return None

        try:
            # Check shared-storage config
            shared_storage_mode = self.charm.config.get("shared-storage", "none")

            if shared_storage_mode == "none":
                logger.info("Shared storage disabled (shared-storage=none)")
                return None

            # For LXC mode, workers use /var/lib/concourse-worker/ for their own data
            # and access shared binaries/keys from /var/lib/concourse/
            if shared_storage_mode == "lxc":
                # Worker's own writable directory
                worker_base_path = Path("/var/lib/concourse-worker")
                worker_base_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Worker base directory: {worker_base_path}")

                # Shared storage path (for binaries and keys) - REQUIRED
                storage_path = Path("/var/lib/concourse")
                # Create the directory if it doesn't exist - the marker file will indicate
                # when the actual mount is ready. This allows the charm to initialize
                # storage coordinator even before the LXC mount is added.
                if not storage_path.exists():
                    logger.info(
                        f"Creating {storage_path} directory for LXC shared storage"
                    )
                    storage_path.mkdir(parents=True, exist_ok=True)

                # Check for LXC shared storage marker BEFORE initializing storage
                marker_file = storage_path / ".lxc_shared_storage"
                if not marker_file.exists():
                    logger.info(
                        "LXC shared storage mode configured but marker file not found. "
                        f"Waiting for {marker_file} to appear."
                    )
                    return None
            else:
                logger.info(f"Unknown shared-storage mode: {shared_storage_mode}")
                return None

            # Get filesystem ID for validation
            filesystem_id = get_filesystem_id(storage_path)

            # Initialize SharedStorage
            shared_storage = SharedStorage(
                volume_path=storage_path, filesystem_id=filesystem_id
            )
            logger.info(f"Initialized shared storage at: {storage_path}")
            logger.info(f"  - Filesystem ID: {shared_storage.filesystem_id}")

            # Initialize LockCoordinator (workers don't acquire, just check)
            lock_coordinator = LockCoordinator(
                lock_path=shared_storage.lock_file_path,
                holder_unit=self.charm.unit.name,
                timeout_seconds=600,  # 10 minutes
            )

            # Initialize StorageCoordinator (worker: waits for binaries)
            self.storage_coordinator = StorageCoordinator(
                storage=shared_storage,
                lock=lock_coordinator,
                is_leader=False,  # Workers wait for download
            )

            # Create worker-specific directory under /var/lib/concourse-worker/
            # (not under shared storage to avoid write conflicts)
            worker_path = worker_base_path / self.charm.unit.name

            # T066: Handle concurrent starts gracefully with existence checks
            try:
                if worker_path.exists():
                    logger.info(f"Worker directory already exists: {worker_path}")
                else:
                    logger.info(f"Creating new worker directory: {worker_path}")

                worker_path.mkdir(parents=True, exist_ok=True)
                work_dir = worker_path / "work_dir"
                work_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning(
                    f"Error creating worker directory (concurrency issue?): {e}"
                )
                # Retry once after short delay
                import time

                time.sleep(1)
                worker_path.mkdir(parents=True, exist_ok=True)
                work_dir = worker_path / "work_dir"
                work_dir.mkdir(parents=True, exist_ok=True)

            self.worker_directory = WorkerDirectory(
                unit_name=self.charm.unit.name,
                path=worker_path,
                state_file=worker_path / "state.json",
                work_dir=work_dir,
            )
            logger.info(f"Created worker directory: {self.worker_directory.path}")
            logger.info(f"  - Work dir: {self.worker_directory.work_dir}")
            logger.info(f"  - State file: {self.worker_directory.state_file}")

            logger.info("Storage coordinator initialized for worker unit")
            return self.storage_coordinator

        except Exception as e:
            logger.error(f"Failed to initialize shared storage: {e}")
            # Non-fatal: fall back to local installation
            return None

    def _get_worker_config_path(self) -> str:
        """Get the worker configuration file path.

        Always use local worker-specific path to avoid conflicts in shared storage.
        """
        # Local mode: config under /var/lib/concourse-worker
        return CONCOURSE_WORKER_CONFIG_FILE

    def setup_systemd_service(self):
        """Create systemd service file for Concourse worker"""
        config_file_path = self._get_worker_config_path()

        worker_service = f"""[Unit]
Description=Concourse CI Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory={CONCOURSE_DATA_DIR}
EnvironmentFile=-{config_file_path}
EnvironmentFile=-/etc/default/concourse
ExecStart={CONCOURSE_BIN} worker
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

        try:
            # Ensure /etc/default/concourse exists (required by systemd service)
            default_config = Path("/etc/default/concourse")
            if not default_config.exists():
                default_config.touch()
                os.chmod("/etc/default/concourse", 0o644)
                logger.info("Created /etc/default/concourse")

            worker_path = Path(SYSTEMD_SERVICE_DIR) / "concourse-worker.service"
            worker_path.write_text(worker_service)
            os.chmod(worker_path, 0o644)

            # Reload systemd to recognize new service files
            subprocess.run(["systemctl", "daemon-reload"], check=True)

            logger.info("Worker systemd service created")
        except Exception as e:
            logger.error(f"Failed to create systemd service: {e}")
            raise

    def configure_containerd_for_gpu(self):
        """
        Configure Concourse worker's containerd for GPU runtime

        Creates custom containerd config and GPU wrapper for automatic GPU injection.
        Supports both NVIDIA and AMD GPUs.
        """
        if not self._is_gpu_enabled():
            logger.info("GPU not enabled, skipping containerd GPU configuration")
            return

        compute_runtime = self._detect_gpu_type()
        if not compute_runtime:
            logger.warning("GPU enabled but no supported GPU detected")
            return

        logger.info(f"Configuring containerd for {compute_runtime.upper()} GPU")

        gpu_containerd_config = Path(CONCOURSE_DATA_DIR) / "containerd-gpu.toml"

        try:
            if compute_runtime == "cuda":
                gpu_config = """version = 3

oom_score = -999
disabled_plugins = ["io.containerd.grpc.v1.cri", "io.containerd.snapshotter.v1.aufs", "io.containerd.snapshotter.v1.btrfs", "io.containerd.snapshotter.v1.zfs"]

# Configure default runtime to use nvidia-container-runtime
[plugins]
  [plugins."io.containerd.runtime.v2.task"]
    runtime = "/usr/bin/nvidia-container-runtime"
    runtime_root = ""
    shim = "containerd-shim-runc-v2"
"""
                gpu_containerd_config.write_text(gpu_config)
                os.chmod(gpu_containerd_config, 0o644)
                logger.info(
                    f"Created NVIDIA containerd config at {gpu_containerd_config}"
                )

                self._ensure_nvidia_tools()
                self._configure_nvidia_runtime()

            elif compute_runtime == "rocm":
                gpu_config = """version = 3

oom_score = -999
disabled_plugins = ["io.containerd.grpc.v1.cri", "io.containerd.snapshotter.v1.aufs", "io.containerd.snapshotter.v1.btrfs", "io.containerd.snapshotter.v1.zfs"]

# Configure default runtime for AMD GPUs
# AMD Container Toolkit CDI spec provides device information at /etc/cdi/amd.json
# Devices are made available via LXD GPU passthrough (/dev/dri/*)
[plugins]
  [plugins."io.containerd.runtime.v2.task"]
    runtime = "runc"
    runtime_root = ""
    shim = "containerd-shim-runc-v2"
"""
                gpu_containerd_config.write_text(gpu_config)
                os.chmod(gpu_containerd_config, 0o644)
                logger.info(f"Created AMD containerd config at {gpu_containerd_config}")

                self._ensure_rocm_tools()

            self._install_gpu_wrapper()

        except Exception as e:
            logger.error(f"Failed to configure worker containerd for GPU: {e}")
            raise

    def _ensure_nvidia_tools(self):
        """
        Ensure NVIDIA utilities and container toolkit are installed

        Automatically sets up NVIDIA repository and installs required packages:
        - nvidia-utils (for nvidia-smi and driver libraries)
        - nvidia-container-toolkit (for GPU container support)
        """
        import subprocess

        try:
            # Check if nvidia-smi is available
            result = subprocess.run(["which", "nvidia-smi"], capture_output=True)

            if result.returncode != 0:
                logger.info("Installing nvidia-utils...")

                # Update apt cache
                subprocess.run(
                    ["apt-get", "update"], check=True, capture_output=True, timeout=120
                )

                # Install nvidia-utils (requires Ubuntu restricted repo)
                # Detect the latest driver version available
                subprocess.run(
                    ["apt-get", "install", "-y", "nvidia-utils-580"],
                    check=True,
                    capture_output=True,
                    timeout=300,
                )

                logger.info("nvidia-utils installed successfully")
            else:
                logger.info("nvidia-utils already installed")

            # Check if nvidia-container-toolkit is installed
            result = subprocess.run(
                ["which", "nvidia-container-toolkit"], capture_output=True
            )

            if result.returncode != 0:
                logger.info("Setting up NVIDIA Container Toolkit repository...")

                # Ensure curl and gnupg are installed
                subprocess.run(
                    ["apt-get", "install", "-y", "curl", "gnupg"],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )

                # Add NVIDIA GPG key (using separate steps to avoid redirect issues)
                key_data = subprocess.run(
                    [
                        "curl",
                        "-fsSL",
                        "https://nvidia.github.io/libnvidia-container/gpgkey",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                ).stdout

                dearmored_key = subprocess.run(
                    ["gpg", "--dearmor"],
                    input=key_data,
                    check=True,
                    capture_output=True,
                ).stdout

                keyring_path = Path(
                    "/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
                )
                keyring_path.write_bytes(dearmored_key)
                os.chmod(keyring_path, 0o644)

                # Detect architecture
                arch_result = subprocess.run(
                    ["dpkg", "--print-architecture"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                arch = arch_result.stdout.strip()

                # Add NVIDIA repository (only generic deb repo - distro-specific repos don't exist)
                repo_content = f"deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/{arch} /\n"
                Path(
                    "/etc/apt/sources.list.d/nvidia-container-toolkit.list"
                ).write_text(repo_content)
                os.chmod("/etc/apt/sources.list.d/nvidia-container-toolkit.list", 0o644)

                logger.info("NVIDIA repository added successfully")

                # Update apt cache with new repository
                subprocess.run(
                    ["apt-get", "update"], check=True, capture_output=True, timeout=120
                )

                # Install nvidia-container-toolkit
                logger.info("Installing nvidia-container-toolkit...")
                subprocess.run(
                    ["apt-get", "install", "-y", "nvidia-container-toolkit"],
                    check=True,
                    capture_output=True,
                    timeout=300,
                )

                logger.info("nvidia-container-toolkit installed successfully")
            else:
                logger.info("nvidia-container-toolkit already installed")

            # Configure nvidia-container-toolkit runtime
            logger.info("Configuring nvidia-container-toolkit...")
            subprocess.run(
                ["nvidia-ctk", "runtime", "configure", "--runtime=containerd"],
                check=False,  # Don't fail if already configured
                capture_output=True,
            )

            logger.info("NVIDIA tools installation and configuration complete")

        except subprocess.TimeoutExpired:
            logger.error("Timeout while installing NVIDIA tools")
            raise
        except Exception as e:
            logger.error(f"Failed to ensure NVIDIA tools: {e}")
            raise

    def _ensure_rocm_tools(self):
        """
        Ensure ROCm utilities are installed for AMD GPUs

        Automatically sets up ROCm repository and installs required packages:
        - rocm-smi (for GPU detection and management)
        - rocm-dev (for development libraries if needed)
        """
        import subprocess

        try:
            result = subprocess.run(["which", "rocm-smi"], capture_output=True)

            if result.returncode != 0:
                logger.info("Installing ROCm tools...")

                subprocess.run(
                    ["apt-get", "update"], check=True, capture_output=True, timeout=120
                )

                subprocess.run(
                    ["apt-get", "install", "-y", "curl", "gnupg"],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )

                key_data = subprocess.run(
                    ["curl", "-fsSL", "https://repo.radeon.com/rocm/rocm.gpg.key"],
                    check=True,
                    capture_output=True,
                    timeout=60,
                ).stdout

                dearmored_key = subprocess.run(
                    ["gpg", "--dearmor"],
                    input=key_data,
                    check=True,
                    capture_output=True,
                ).stdout

                keyring_path = Path("/usr/share/keyrings/rocm-keyring.gpg")
                keyring_path.write_bytes(dearmored_key)
                os.chmod(keyring_path, 0o644)

                arch_result = subprocess.run(
                    ["dpkg", "--print-architecture"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                arch = arch_result.stdout.strip()

                version_result = subprocess.run(
                    ["lsb_release", "-sc"], capture_output=True, text=True, check=True
                )
                ubuntu_version = version_result.stdout.strip()

                repo_content = f"deb [arch={arch} signed-by=/usr/share/keyrings/rocm-keyring.gpg] https://repo.radeon.com/amdgpu/latest/ubuntu {ubuntu_version} main\n"
                repo_file = Path("/etc/apt/sources.list.d/amdgpu.list")
                repo_file.write_text(repo_content)
                os.chmod(repo_file, 0o644)

                rocm_repo_content = f"deb [arch={arch} signed-by=/usr/share/keyrings/rocm-keyring.gpg] https://repo.radeon.com/rocm/apt/latest {ubuntu_version} main\n"
                rocm_repo_file = Path("/etc/apt/sources.list.d/rocm.list")
                rocm_repo_file.write_text(rocm_repo_content)
                os.chmod(rocm_repo_file, 0o644)

                logger.info("ROCm repository added successfully")

                subprocess.run(
                    ["apt-get", "update"], check=True, capture_output=True, timeout=120
                )

                logger.info("Installing rocm-smi...")
                subprocess.run(
                    ["apt-get", "install", "-y", "rocm-smi"],
                    check=True,
                    capture_output=True,
                    timeout=300,
                )

                logger.info("rocm-smi installed successfully")
            else:
                logger.info("rocm-smi already installed")

            self._install_amd_container_toolkit()

            logger.info("ROCm tools installation and configuration complete")

        except subprocess.TimeoutExpired:
            logger.error("Timeout while installing ROCm tools")
            raise
        except Exception as e:
            logger.error(f"Failed to ensure ROCm tools: {e}")
            raise

    def _install_amd_container_toolkit(self):
        """
        Install AMD Container Toolkit for ROCm container runtime support

        Similar to nvidia-container-toolkit, this provides proper GPU device
        injection and runtime support for AMD ROCm GPUs in containers.

        Installation steps:
        1. Add AMD Container Toolkit repository
        2. Install amd-container-toolkit package
        3. Configure Docker/containerd runtime
        """
        import subprocess

        try:
            result = subprocess.run(
                ["dpkg", "-l", "amd-container-toolkit"], capture_output=True, text=True
            )

            if "ii  amd-container-toolkit" in result.stdout:
                logger.info("amd-container-toolkit already installed")
                return

            logger.info("Installing AMD Container Toolkit...")

            subprocess.run(
                ["apt-get", "update"], check=True, capture_output=True, timeout=120
            )

            subprocess.run(
                ["apt-get", "install", "-y", "curl", "gnupg"],
                check=True,
                capture_output=True,
                timeout=120,
            )

            keyring_dir = Path("/etc/apt/keyrings")
            keyring_dir.mkdir(parents=True, exist_ok=True, mode=0o755)

            key_data = subprocess.run(
                ["curl", "-fsSL", "https://repo.radeon.com/rocm/rocm.gpg.key"],
                check=True,
                capture_output=True,
                timeout=60,
            ).stdout

            dearmored_key = subprocess.run(
                ["gpg", "--dearmor"],
                input=key_data,
                check=True,
                capture_output=True,
            ).stdout

            keyring_path = Path("/etc/apt/keyrings/rocm.gpg")
            keyring_path.write_bytes(dearmored_key)
            os.chmod(keyring_path, 0o644)

            version_result = subprocess.run(
                ["lsb_release", "-sc"], capture_output=True, text=True, check=True
            )
            ubuntu_version = version_result.stdout.strip()

            if ubuntu_version == "jammy":
                repo_line = "jammy"
            elif ubuntu_version == "noble":
                repo_line = "noble"
            else:
                logger.warning(
                    f"Ubuntu version {ubuntu_version} may not be officially supported. Using noble repository."
                )
                repo_line = "noble"

            repo_content = f"deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/amd-container-toolkit/apt/ {repo_line} main\n"
            repo_file = Path("/etc/apt/sources.list.d/amd-container-toolkit.list")
            repo_file.write_text(repo_content)
            os.chmod(repo_file, 0o644)

            logger.info("AMD Container Toolkit repository added")

            subprocess.run(
                ["apt-get", "update"], check=True, capture_output=True, timeout=120
            )

            logger.info("Installing amd-container-toolkit package...")
            subprocess.run(
                ["apt-get", "install", "-y", "amd-container-toolkit"],
                check=True,
                capture_output=True,
                timeout=300,
            )

            logger.info("Generating CDI specification for containerd...")
            cdi_dir = Path("/etc/cdi")
            cdi_dir.mkdir(parents=True, exist_ok=True, mode=0o755)

            subprocess.run(
                ["amd-ctk", "cdi", "generate", "--output=/etc/cdi/amd.json"],
                check=True,
                capture_output=True,
                timeout=60,
            )

            logger.info("Validating CDI specification...")
            subprocess.run(
                ["amd-ctk", "cdi", "validate", "--path=/etc/cdi/amd.json"],
                check=True,
                capture_output=True,
                timeout=30,
            )

            logger.info(
                "AMD Container Toolkit installed and CDI configured successfully"
            )

        except subprocess.TimeoutExpired:
            logger.error("Timeout while installing AMD Container Toolkit")
            raise
        except Exception as e:
            logger.error(f"Failed to install AMD Container Toolkit: {e}")
            raise

    def _install_gpu_wrapper(self):
        """
        Install GPU wrapper script for AMD or NVIDIA GPUs

        The wrapper intercepts runc calls and injects GPU devices and folder mounts.
        - For NVIDIA: calls nvidia-container-runtime for device injection
        - For AMD: directly injects /dev/dri devices into OCI spec
        """
        import subprocess
        import shutil

        compute_runtime = self._detect_gpu_type()
        if not compute_runtime:
            logger.warning("No GPU detected, skipping GPU wrapper installation")
            return

        Path("/opt/bin").mkdir(parents=True, exist_ok=True)

        concourse_runc = Path("/opt/bin/runc")
        concourse_runc_real = Path("/opt/bin/runc.real")

        try:
            logger.info("Ensuring jq is installed...")
            subprocess.run(
                ["apt-get", "install", "-y", "jq"],
                check=False,
                capture_output=True,
                timeout=60,
            )

            if compute_runtime == "cuda":
                wrapper_name = "runc-gpu-wrapper"
                wrapper_path = Path("/opt/bin/runc-gpu-wrapper")
                nvidia_runtime = Path("/usr/bin/nvidia-container-runtime")
                nvidia_runtime_real = Path("/usr/bin/nvidia-container-runtime.real")

                charm_wrapper = self.charm.charm_dir / "hooks" / wrapper_name
                if not charm_wrapper.exists():
                    raise FileNotFoundError(
                        f"NVIDIA GPU wrapper not found at {charm_wrapper}"
                    )

                shutil.copy2(str(charm_wrapper), str(wrapper_path))
                os.chmod(wrapper_path, 0o755)
                logger.info(f"NVIDIA GPU wrapper installed at {wrapper_path}")

                if nvidia_runtime.exists() and not nvidia_runtime_real.exists():
                    logger.info(
                        f"Backing up nvidia-container-runtime to {nvidia_runtime_real}"
                    )
                    subprocess.run(
                        ["cp", str(nvidia_runtime), str(nvidia_runtime_real)],
                        check=True,
                    )

            elif compute_runtime == "rocm":
                wrapper_name = "runc-amd-wrapper"
                wrapper_path = Path("/opt/bin/runc-amd-wrapper")

                charm_wrapper = self.charm.charm_dir / "hooks" / wrapper_name
                if not charm_wrapper.exists():
                    raise FileNotFoundError(
                        f"AMD GPU wrapper not found at {charm_wrapper}"
                    )

                shutil.copy2(str(charm_wrapper), str(wrapper_path))
                os.chmod(wrapper_path, 0o755)
                logger.info(f"AMD GPU wrapper installed at {wrapper_path}")

            else:
                logger.error(f"Unknown compute runtime: {compute_runtime}")
                return

            # Set up the wrapper symlinks
            concourse_runc_source = Path("/var/lib/concourse/bin/runc")
            if not concourse_runc_real.exists():
                if concourse_runc_source.exists():
                    logger.info(
                        f"Copying runc from Concourse binaries to {concourse_runc_real}"
                    )
                    subprocess.run(
                        ["cp", str(concourse_runc_source), str(concourse_runc_real)],
                        check=True,
                    )
                else:
                    logger.warning(
                        "Concourse runc not available yet, skipping GPU wrapper setup"
                    )
                    return

            if concourse_runc.exists():
                if concourse_runc.is_symlink():
                    logger.info("Replacing existing symlink to point to GPU wrapper")
                    concourse_runc.unlink()
                else:
                    logger.warning(
                        f"Expected symlink but found file at {concourse_runc}"
                    )
                    concourse_runc.unlink()

            logger.info(f"Creating symlink: {concourse_runc} -> {wrapper_path}")
            subprocess.run(
                ["ln", "-sf", str(wrapper_path), str(concourse_runc)], check=True
            )

            var_lib_runc = Path("/var/lib/concourse/bin/runc")
            logger.info(f"Creating symlink: {var_lib_runc} -> {wrapper_path}")
            subprocess.run(
                ["ln", "-sf", str(wrapper_path), str(var_lib_runc)], check=True
            )

            logger.info(
                f"{compute_runtime.upper()} GPU wrapper installed and symlinked successfully"
            )

        except subprocess.TimeoutExpired:
            logger.error("Timeout while installing dependencies")
            raise
        except Exception as e:
            logger.error(f"Failed to install GPU wrapper: {e}")
            raise

    def _configure_nvidia_runtime(self):
        """
        Configure nvidia-container-runtime to use the real runc binary

        This prevents infinite loops where nvidia-container-runtime calls our wrapper
        """
        import subprocess

        nvidia_config = Path("/etc/nvidia-container-runtime/config.toml")

        if not nvidia_config.exists():
            logger.warning(
                f"NVIDIA container runtime config not found at {nvidia_config}"
            )
            return

        try:
            # Configure nvidia-container-runtime to use the real runc
            logger.info("Configuring nvidia-container-runtime to use real runc")
            subprocess.run(
                [
                    "sed",
                    "-i",
                    's|runtimes = \\["runc", "crun"\\]|runtimes = ["/opt/bin/runc.real", "crun"]|',
                    str(nvidia_config),
                ],
                check=True,
            )
            logger.info("NVIDIA container runtime configured successfully")

        except Exception as e:
            logger.error(f"Failed to configure nvidia-container-runtime: {e}")
            # Non-fatal - wrapper might still work
            pass

    def update_config(
        self, tsa_host: str = "127.0.0.1:2222", keys_dir: Optional[str] = None
    ):
        """Update Concourse worker configuration (T025: use shared storage work_dir if available)"""
        # Ensure shared storage is initialized if configured (T067)
        if (
            not self.worker_directory
            and self.charm.config.get("shared-storage", "none") != "none"
        ):
            self.initialize_shared_storage()

        if keys_dir:
            keys_dir = Path(keys_dir)
        else:
            from concourse_common import WORKER_KEYS_DIR

            keys_dir = Path(WORKER_KEYS_DIR)

        # Use worker-specific directory from shared storage if available (T025)
        if self.worker_directory:
            worker_dir = self.worker_directory.work_dir
            logger.info(f"Using shared storage work_dir: {worker_dir}")
        else:
            worker_dir = Path(CONCOURSE_DATA_DIR) / "worker"
            worker_dir.mkdir(
                parents=True, exist_ok=True
            )  # Create parent directories too
            logger.info(f"Using local work_dir: {worker_dir}")

        config = {
            "CONCOURSE_WORKER_PROCS": str(self.config.get("worker-procs", 1)),
            "CONCOURSE_LOG_LEVEL": self.config.get("log-level", "info"),
            "CONCOURSE_TSA_WORKER_PRIVATE_KEY": str(keys_dir / "worker_key"),
            "CONCOURSE_WORK_DIR": str(worker_dir),
            "CONCOURSE_TSA_HOST": tsa_host,
            "CONCOURSE_TSA_PUBLIC_KEY": str(keys_dir / "tsa_host_key.pub"),
            "CONCOURSE_RUNTIME": "containerd",
            "CONCOURSE_BAGGAGECLAIM_DRIVER": "naive",
            "CONCOURSE_BIND_IP": "127.0.0.1",
            "CONCOURSE_BIND_PORT": "7777",
            "CONCOURSE_CONTAINERD_DNS_PROXY_ENABLE": str(
                self.config.get("containerd-dns-proxy-enable", False)
            ).lower(),
            "CONCOURSE_CONTAINERD_DNS_SERVER": self.config.get(
                "containerd-dns-server", "1.1.1.1,8.8.8.8"
            ),
            # Use custom runc wrapper from /opt/bin
            "CONCOURSE_CONTAINERD_RUNTIME": "/opt/bin/runc",
            "CONCOURSE_RESOURCE_TYPES": str(
                Path(CONCOURSE_DATA_DIR) / "resource-types"
            ),
            # Ensure /opt/bin is in PATH for runc wrapper
            "PATH": "/opt/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin",
        }

        # Add GPU configuration if enabled
        if self._is_gpu_enabled():
            gpu_config_path = Path(CONCOURSE_DATA_DIR) / "containerd-gpu.toml"
            if gpu_config_path.exists():
                config["CONCOURSE_CONTAINERD_CONFIG"] = str(gpu_config_path)
                logger.info(f"Using GPU containerd config: {gpu_config_path}")

        # Combine user-defined tags and GPU tags
        user_tag_config = self.config.get("tag", "")
        user_tags = (
            [t.strip() for t in user_tag_config.split(",") if t.strip()]
            if user_tag_config
            else []
        )

        gpu_tags = self._get_gpu_tags()

        # Merge tags, preserving order and deduplicating
        combined_tags = []
        if user_tags:
            combined_tags.extend(user_tags)
        if gpu_tags:
            combined_tags.extend(gpu_tags)

        seen = set()
        dedup_tags = []
        for t in combined_tags:
            if t not in seen:
                seen.add(t)
                dedup_tags.append(t)

        if dedup_tags:
            config["CONCOURSE_TAG"] = ",".join(dedup_tags)
            logger.info(f"Adding CONCOURSE_TAG: {dedup_tags}")

        # Ensure dataset mount is available via symlink in worker directory
        self._setup_dataset_mount()

        # Write config file
        self._write_config(config)
        logger.info("Worker configuration updated")

    def _setup_dataset_mount(self):
        """Setup dataset directory accessibility check

        Datasets are automatically mounted into GPU worker containers via the OCI spec wrapper.
        This method just validates that the dataset directory exists on the host.
        """
        try:
            dataset_source = Path("/srv/datasets")
            if not dataset_source.exists():
                logger.info(
                    "/srv/datasets not found - tasks will not have dataset access"
                )
                return

            logger.info(f"Dataset directory found at {dataset_source}")
            logger.info(
                "GPU tasks will automatically have /srv/datasets mounted read-only"
            )

        except Exception as e:
            logger.warning(f"Failed to check dataset mount: {e}")

    def _write_config(self, config: dict):
        """Write configuration to file"""
        try:
            config_file_path = self._get_worker_config_path()
            config_lines = [f"{k}={v}" for k, v in config.items()]
            Path(config_file_path).write_text("\n".join(config_lines) + "\n")
            os.chmod(config_file_path, 0o640)
            subprocess.run(
                ["chown", "root:root", config_file_path],
                check=True,
                capture_output=True,
            )
            logger.info(f"Configuration written to {config_file_path}")
        except Exception as e:
            logger.error(f"Failed to write config: {e}")
            raise

    def install_folder_mount_wrapper(self):
        """
        Install folder mounting wrapper for non-GPU workers

        This installs the OCI wrapper that discovers and injects /srv folder mounts.
        For GPU workers, this is handled by the GPU wrapper instead.
        """
        import subprocess
        import shutil

        # Skip if GPU is enabled (GPU wrapper handles both GPU and folders)
        if self._is_gpu_enabled():
            logger.info(
                "Skipping non-GPU wrapper installation (GPU wrapper handles folders)"
            )
            return

        # Use /opt/bin for custom wrappers to keep /var/lib/concourse pure
        wrapper_path = Path("/opt/bin/runc-wrapper")
        concourse_runc = Path("/opt/bin/runc")
        concourse_runc_real = Path("/opt/bin/runc.real")
        runc_real = Path("/usr/bin/runc.real")

        # Ensure /opt/bin exists
        Path("/opt/bin").mkdir(parents=True, exist_ok=True)

        try:
            # Install jq if not present (needed for JSON manipulation)
            logger.info("Ensuring jq is installed...")
            subprocess.run(
                ["apt-get", "install", "-y", "jq"],
                check=False,  # Don't fail if already installed
                capture_output=True,
                timeout=60,
            )

            # Copy folder mounting wrapper from charm hooks directory
            logger.info(f"Installing folder mounting wrapper at {wrapper_path}")
            charm_wrapper = self.charm.charm_dir / "hooks" / "runc-wrapper"

            if not charm_wrapper.exists():
                raise FileNotFoundError(
                    f"Folder mounting wrapper not found at {charm_wrapper}"
                )

            shutil.copy2(str(charm_wrapper), str(wrapper_path))
            os.chmod(wrapper_path, 0o755)
            logger.info("Folder mounting wrapper installed successfully")

            # Backup original runc if not already backed up and if /usr/bin/runc exists
            if not runc_real.exists():
                if Path("/usr/bin/runc").exists():
                    logger.info(f"Backing up original runc to {runc_real}")
                    subprocess.run(["cp", "/usr/bin/runc", str(runc_real)], check=True)
                else:
                    # Use the runc from concourse binaries instead
                    concourse_system_runc = Path("/var/lib/concourse/bin/runc")
                    if concourse_system_runc.exists():
                        logger.info(
                            f"Copying runc from Concourse binaries to {runc_real}"
                        )
                        subprocess.run(
                            ["cp", str(concourse_system_runc), str(runc_real)],
                            check=True,
                        )
                        # Remove original runc to avoid conflict
                        concourse_system_runc.unlink()
                        # Symlink runc in bin folder to wrapper
                        concourse_system_runc.symlink_to(wrapper_path)
                    else:
                        logger.warning(
                            "/usr/bin/runc not found and Concourse runc not available yet"
                        )
                        # Don't fail - we'll retry later when binaries are available
                        return

            # Copy runc to concourse_runc_real if needed
            if not concourse_runc_real.exists() and runc_real.exists():
                logger.info(f"Copying runc to {concourse_runc_real}")
                subprocess.run(
                    ["cp", str(runc_real), str(concourse_runc_real)], check=True
                )

            # Create symlink from concourse runc to wrapper
            if not concourse_runc.exists():
                logger.info(f"Creating symlink: {concourse_runc} -> {wrapper_path}")
                subprocess.run(
                    ["ln", "-sf", str(wrapper_path), str(concourse_runc)], check=True
                )
                logger.info("Folder mounting wrapper symlinked successfully")
            else:
                logger.info("Concourse runc already configured")

            # Also ensure /var/lib/concourse/bin/runc is a symlink to wrapper if it exists as a file
            concourse_bin_runc = Path("/var/lib/concourse/bin/runc")
            if concourse_bin_runc.exists() and not concourse_bin_runc.is_symlink():
                logger.info(f"Replacing {concourse_bin_runc} with symlink to wrapper")
                # Always backup to /opt/bin/runc.real if it exists as a regular file
                # This ensures we capture the binary that Concourse actually downloaded
                subprocess.run(
                    ["cp", str(concourse_bin_runc), str(runc_real)], check=True
                )

                concourse_bin_runc.unlink()
                concourse_bin_runc.symlink_to(wrapper_path)
                logger.info(f"Symlinked {concourse_bin_runc} -> {wrapper_path}")

        except subprocess.TimeoutExpired:
            logger.error("Timeout while installing dependencies")
            raise
        except Exception as e:
            logger.error(f"Failed to install folder mounting wrapper: {e}")
            raise

    def start_service(self):
        """Start Concourse worker service"""
        try:
            subprocess.run(
                ["systemctl", "enable", "concourse-worker.service"], check=True
            )
            subprocess.run(
                ["systemctl", "start", "concourse-worker.service"], check=True
            )
            logger.info("Worker service started")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start worker: {e}")
            raise

    def stop_service(self):
        """Stop Concourse worker service"""
        try:
            subprocess.run(
                ["systemctl", "stop", "concourse-worker.service"], capture_output=True
            )
            subprocess.run(
                ["systemctl", "disable", "concourse-worker.service"],
                capture_output=True,
            )
            logger.info("Worker service stopped")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop worker: {e}")

    def restart_service(self):
        """Restart Concourse worker service"""
        try:
            subprocess.run(
                ["systemctl", "restart", "concourse-worker.service"], check=True
            )
            logger.info("Worker service restarted")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart worker: {e}")
            raise

    def is_running(self) -> bool:
        """Check if worker is running"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "concourse-worker.service"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and result.stdout.strip() == "active"
        except Exception:
            return False

    def _get_gpu_tags(self):
        """
        Generate worker tags based on GPU configuration

        Returns:
            list: GPU tags for worker, or empty list if GPU disabled
        """
        if not self._is_gpu_enabled():
            return []

        compute_runtime = self._detect_gpu_type()
        if not compute_runtime:
            logger.warning("GPU enabled but no supported GPU detected")
            return []

        # Use compute-runtime as the primary tag (cuda or rocm)
        tags = [compute_runtime]

        if compute_runtime == "cuda":
            gpu_info = detect_nvidia_gpus()
        elif compute_runtime == "rocm":
            gpu_info = detect_amd_gpus()
        else:
            return tags

        if not gpu_info:
            return tags

        device_ids_config = self.config.get("gpu-device-ids", "all")

        if device_ids_config == "all":
            gpu_count = gpu_info["count"]
            tags.append(f"gpu-count={gpu_count}")
        else:
            try:
                device_ids = [int(x.strip()) for x in device_ids_config.split(",")]
                max_device = max(device_ids)
                if max_device >= gpu_info["count"]:
                    logger.error(
                        f"Invalid GPU device ID {max_device}, only {gpu_info['count']} GPUs available"
                    )
                    return tags

                gpu_count = len(device_ids)
                tags.append(f"gpu-count={gpu_count}")
                tags.append(f"gpu-devices={device_ids_config}")
            except ValueError:
                logger.error(f"Invalid gpu-device-ids format: {device_ids_config}")
                return tags

        logger.info(f"Generated GPU tags: {tags}")
        return tags

    def get_gpu_status_message(self):
        """
        Get GPU status message for unit status

        Returns:
            str: GPU status message or empty string
        """
        if not self._is_gpu_enabled():
            return ""

        compute_runtime = self._detect_gpu_type()
        if not compute_runtime:
            return ""

        if compute_runtime == "cuda":
            gpu_info = detect_nvidia_gpus()
            vendor_name = "NVIDIA"
        elif compute_runtime == "rocm":
            gpu_info = detect_amd_gpus()
            vendor_name = "AMD"
        else:
            return ""

        if not gpu_info:
            return ""

        device_ids_config = self.config.get("gpu-device-ids", "all")
        if device_ids_config == "all":
            count = gpu_info["count"]
        else:
            try:
                device_ids = [int(x.strip()) for x in device_ids_config.split(",")]
                count = len(device_ids)
            except Exception:
                count = 0

        if count > 0:
            return f" (GPU: {count}x {vendor_name})"

        return ""
