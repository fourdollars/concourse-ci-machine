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
