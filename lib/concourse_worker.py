#!/usr/bin/env python3
"""
Concourse Worker Helper Library
"""

import logging
import os
import subprocess
from pathlib import Path

from concourse_common import (
    CONCOURSE_BIN,
    CONCOURSE_CONFIG_FILE,
    CONCOURSE_DATA_DIR,
    SYSTEMD_SERVICE_DIR,
    KEYS_DIR,
    detect_nvidia_gpus,
    verify_nvidia_container_runtime,
)

logger = logging.getLogger(__name__)


class ConcourseWorkerHelper:
    """Helper class for Concourse worker operations"""

    def __init__(self, charm):
        self.charm = charm
        self.model = charm.model
        self.config = charm.model.config

    def setup_systemd_service(self):
        """Create systemd service file for Concourse worker"""
        worker_service = f"""[Unit]
Description=Concourse CI Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory={CONCOURSE_DATA_DIR}
EnvironmentFile={CONCOURSE_CONFIG_FILE}
EnvironmentFile=/etc/default/concourse
ExecStart={CONCOURSE_BIN} worker
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

        try:
            worker_path = Path(SYSTEMD_SERVICE_DIR) / "concourse-worker.service"
            worker_path.write_text(worker_service)
            os.chmod(worker_path, 0o644)

            # Reload systemd to recognize new service files
            subprocess.run(["systemctl", "daemon-reload"], check=True)

            logger.info(f"Worker systemd service created")
        except Exception as e:
            logger.error(f"Failed to create systemd service: {e}")
            raise
    
    def configure_containerd_for_gpu(self):
        """
        Configure Concourse worker's containerd to use NVIDIA runtime
        
        Creates a custom containerd config with GPU support
        """
        if not self.config.get("enable-gpu", False):
            logger.info("GPU not enabled, skipping containerd GPU configuration")
            return
        
        # Create a custom GPU-enabled containerd config
        # We'll use a separate file that worker will be configured to use
        gpu_containerd_config = Path(CONCOURSE_DATA_DIR) / "containerd-gpu.toml"
        
        try:
            # Create new config with NVIDIA runtime support
            # For containerd v3, we need to set the runtime at a different level
            # The simplest approach: symlink runc to nvidia-container-runtime
            # But first, let's try configuring the runtime binary path correctly
            gpu_config = """version = 3

oom_score = -999
disabled_plugins = ["io.containerd.grpc.v1.cri", "io.containerd.snapshotter.v1.aufs", "io.containerd.snapshotter.v1.btrfs", "io.containerd.snapshotter.v1.zfs"]

# Configure default runtime to use nvidia-container-runtime
# In containerd v3, runtime configuration is at the plugin level
[plugins]
  [plugins."io.containerd.runtime.v2.task"]
    # Use nvidia-container-runtime as the OCI runtime
    runtime = "/usr/bin/nvidia-container-runtime"
    runtime_root = ""
    shim = "containerd-shim-runc-v2"
"""
            
            # Write GPU config
            gpu_containerd_config.write_text(gpu_config)
            os.chmod(gpu_containerd_config, 0o644)
            logger.info(f"Created GPU containerd config at {gpu_containerd_config}")
            
        except Exception as e:
            logger.error(f"Failed to configure worker containerd for GPU: {e}")
            raise

    def update_config(self, tsa_host: str = "127.0.0.1:2222"):
        """Update Concourse worker configuration"""
        keys_dir = Path(KEYS_DIR)
        worker_dir = Path(CONCOURSE_DATA_DIR) / "worker"
        worker_dir.mkdir(exist_ok=True)

        config = {
            "CONCOURSE_WORKER_PROCS": str(self.config.get("worker-procs", 1)),
            "CONCOURSE_LOG_LEVEL": self.config.get("log-level", "info"),
            "CONCOURSE_TSA_WORKER_PRIVATE_KEY": str(keys_dir / "worker_key"),
            "CONCOURSE_WORK_DIR": str(worker_dir),
            "CONCOURSE_TSA_HOST": tsa_host,
            "CONCOURSE_TSA_PUBLIC_KEY": str(keys_dir / "tsa_host_key.pub"),
            "CONCOURSE_RUNTIME": "containerd",
            "CONCOURSE_BAGGAGECLAIM_DRIVER": "naive",
            "CONCOURSE_CONTAINERD_DNS_PROXY_ENABLE": str(
                self.config.get("containerd-dns-proxy-enable", False)
            ).lower(),
            "CONCOURSE_CONTAINERD_DNS_SERVER": self.config.get(
                "containerd-dns-server", "1.1.1.1,8.8.8.8"
            ),
        }
        
        # Add GPU configuration if enabled
        if self.config.get("enable-gpu", False):
            gpu_config_path = Path(CONCOURSE_DATA_DIR) / "containerd-gpu.toml"
            if gpu_config_path.exists():
                config["CONCOURSE_CONTAINERD_CONFIG"] = str(gpu_config_path)
                logger.info(f"Using GPU containerd config: {gpu_config_path}")
        
        # Add GPU tags if enabled
        gpu_tags = self._get_gpu_tags()
        if gpu_tags:
            config["CONCOURSE_TAG"] = ",".join(gpu_tags)
            logger.info(f"Adding GPU tags: {gpu_tags}")

        # Write config file
        self._write_config(config)
        logger.info("Worker configuration updated")

    def _write_config(self, config: dict):
        """Write configuration to file"""
        try:
            config_lines = [f"{k}={v}" for k, v in config.items()]
            Path(CONCOURSE_CONFIG_FILE).write_text("\n".join(config_lines) + "\n")
            os.chmod(CONCOURSE_CONFIG_FILE, 0o640)
            subprocess.run(
                ["chown", "root:root", CONCOURSE_CONFIG_FILE],
                check=True,
                capture_output=True,
            )
            logger.info(f"Configuration written to {CONCOURSE_CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Failed to write config: {e}")
            raise

    def start_service(self):
        """Start Concourse worker service"""
        try:
            subprocess.run(["systemctl", "enable", "concourse-worker"], check=True)
            subprocess.run(["systemctl", "start", "concourse-worker"], check=True)
            logger.info("Worker service started")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start worker: {e}")
            raise

    def stop_service(self):
        """Stop Concourse worker service"""
        try:
            subprocess.run(
                ["systemctl", "stop", "concourse-worker"], capture_output=True
            )
            subprocess.run(
                ["systemctl", "disable", "concourse-worker"], capture_output=True
            )
            logger.info("Worker service stopped")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop worker: {e}")

    def restart_service(self):
        """Restart Concourse worker service"""
        try:
            subprocess.run(["systemctl", "restart", "concourse-worker"], check=True)
            logger.info("Worker service restarted")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart worker: {e}")
            raise

    def is_running(self) -> bool:
        """Check if worker is running"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "concourse-worker"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and result.stdout.strip() == "active"
        except:
            return False
    
    def _get_gpu_tags(self):
        """
        Generate worker tags based on GPU configuration
        
        Returns:
            list: GPU tags for worker, or empty list if GPU disabled
        """
        if not self.config.get("enable-gpu", False):
            return []
        
        # Verify GPU availability
        gpu_info = detect_nvidia_gpus()
        if not gpu_info:
            logger.warning("GPU enabled but no NVIDIA GPUs detected")
            return []
        
        if not verify_nvidia_container_runtime():
            logger.warning("GPU enabled but nvidia-container-runtime not available")
            return []
        
        tags = ["gpu"]
        
        # Add GPU type
        tags.append("gpu-type=nvidia")
        
        # Handle device selection
        device_ids_config = self.config.get("gpu-device-ids", "all")
        
        if device_ids_config == "all":
            # Expose all GPUs
            gpu_count = gpu_info["count"]
            tags.append(f"gpu-count={gpu_count}")
        else:
            # Parse specific device IDs
            try:
                device_ids = [int(x.strip()) for x in device_ids_config.split(",")]
                # Validate device IDs exist
                max_device = max(device_ids)
                if max_device >= gpu_info["count"]:
                    logger.error(f"Invalid GPU device ID {max_device}, only {gpu_info['count']} GPUs available")
                    return []
                
                gpu_count = len(device_ids)
                tags.append(f"gpu-count={gpu_count}")
                tags.append(f"gpu-devices={device_ids_config}")
            except ValueError as e:
                logger.error(f"Invalid gpu-device-ids format: {device_ids_config}")
                return []
        
        logger.info(f"Generated GPU tags: {tags}")
        return tags
    
    def get_gpu_status_message(self):
        """
        Get GPU status message for unit status
        
        Returns:
            str: GPU status message or empty string
        """
        if not self.config.get("enable-gpu", False):
            return ""
        
        gpu_info = detect_nvidia_gpus()
        if not gpu_info:
            return ""
        
        device_ids_config = self.config.get("gpu-device-ids", "all")
        if device_ids_config == "all":
            count = gpu_info["count"]
        else:
            try:
                device_ids = [int(x.strip()) for x in device_ids_config.split(",")]
                count = len(device_ids)
            except:
                count = 0
        
        if count > 0:
            gpu_name = gpu_info["devices"][0]["name"]
            return f" (GPU: {count}x NVIDIA)"
        
        return ""
