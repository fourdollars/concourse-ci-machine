#!/usr/bin/env python3
"""
Concourse Prometheus Exporter Helper Library
Manages the lifecycle of the Concourse Prometheus Exporter service
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from concourse_common import CONCOURSE_DATA_DIR, SYSTEMD_SERVICE_DIR

logger = logging.getLogger(__name__)

# Exporter constants
EXPORTER_SCRIPT_PATH = "/usr/local/bin/concourse-exporter.py"
EXPORTER_ENV_FILE = "/etc/concourse-exporter.env"
EXPORTER_SERVICE_NAME = "concourse-exporter"
EXPORTER_SERVICE_FILE = f"{SYSTEMD_SERVICE_DIR}/{EXPORTER_SERVICE_NAME}.service"
EXPORTER_PORT = 9358


class ConcourseExporterHelper:
    """Helper class for managing Concourse Prometheus Exporter"""

    def __init__(self, charm):
        self.charm = charm
        self.model = charm.model
        self.config = charm.model.config
        self.unit = charm.unit

    def install_exporter(self) -> bool:
        """
        Install the Concourse Prometheus exporter script and dependencies.

        Returns:
            bool: True if installation successful, False otherwise
        """
        try:
            # Install Python dependencies
            logger.info("Installing exporter dependencies...")
            subprocess.run(
                [
                    "apt-get",
                    "install",
                    "-y",
                    "python3-prometheus-client",
                    "python3-requests",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            # Copy exporter script from templates
            template_script = (
                Path(self.charm.charm_dir)
                / "src"
                / "templates"
                / "concourse-exporter.py"
            )

            if not template_script.exists():
                logger.error(f"Exporter template not found at {template_script}")
                return False

            logger.info(f"Copying exporter script to {EXPORTER_SCRIPT_PATH}")
            shutil.copy2(template_script, EXPORTER_SCRIPT_PATH)
            os.chmod(EXPORTER_SCRIPT_PATH, 0o755)

            # Install fly CLI if not already present
            if not self._ensure_fly_cli():
                logger.error("Failed to install fly CLI")
                return False

            logger.info("Exporter installation completed successfully")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install exporter dependencies: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Exporter installation failed: {e}")
            return False

    def _ensure_fly_cli(self) -> bool:
        """Ensure fly CLI is installed for API access"""
        fly_path = "/usr/local/bin/fly"

        if os.path.exists(fly_path):
            logger.info("fly CLI already installed")
            return True

        try:
            # Get web URL from unit address
            unit_address = self.charm.model.get_binding("peers").network.bind_address
            concourse_url = f"http://{unit_address}:8080"

            logger.info(f"Downloading fly CLI from {concourse_url}")

            # Wait a bit for web server to be ready
            import time

            time.sleep(5)

            # Download fly CLI
            subprocess.run(
                [
                    "wget",
                    "-q",
                    "-O",
                    fly_path,
                    f"{concourse_url}/api/v1/cli?arch=amd64&platform=linux",
                ],
                check=True,
                timeout=60,
            )
            os.chmod(fly_path, 0o755)

            logger.info("fly CLI installed successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to install fly CLI: {e}")
            return False

    def create_systemd_service(self) -> bool:
        """
        Create systemd service file for the exporter.

        Returns:
            bool: True if service creation successful, False otherwise
        """
        try:
            service_content = f"""[Unit]
Description=Concourse CI Prometheus Exporter
After=network.target concourse-server.service
Wants=concourse-server.service

[Service]
Type=simple
User=root
EnvironmentFile={EXPORTER_ENV_FILE}
ExecStart=/usr/bin/python3 {EXPORTER_SCRIPT_PATH}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

            logger.info(f"Creating systemd service at {EXPORTER_SERVICE_FILE}")
            with open(EXPORTER_SERVICE_FILE, "w") as f:
                f.write(service_content)

            # Reload systemd daemon
            subprocess.run(["systemctl", "daemon-reload"], check=True)

            logger.info("Systemd service created successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to create systemd service: {e}")
            return False

    def update_env_config(self) -> bool:
        """
        Update environment configuration file for the exporter.

        Returns:
            bool: True if config update successful, False otherwise
        """
        try:
            # Get admin password from peer data
            peer_relation = self.charm.model.get_relation("peers")
            if not peer_relation:
                logger.error("Peer relation not found")
                return False

            admin_password = peer_relation.data[self.charm.app].get(
                "admin-password", ""
            )

            if not admin_password:
                logger.error("Admin password not found in peer data")
                return False

            # Get unit address
            unit_address = self.charm.model.get_binding("peers").network.bind_address
            concourse_url = f"http://{unit_address}:8080"

            # Create environment file
            env_content = f"""CONCOURSE_URL={concourse_url}
CONCOURSE_TEAM=main
CONCOURSE_USERNAME=admin
CONCOURSE_PASSWORD={admin_password}
EXPORTER_PORT={EXPORTER_PORT}
SCRAPE_INTERVAL=30
"""

            logger.info(f"Updating exporter configuration at {EXPORTER_ENV_FILE}")
            with open(EXPORTER_ENV_FILE, "w") as f:
                f.write(env_content)

            os.chmod(EXPORTER_ENV_FILE, 0o600)  # Secure file permissions

            logger.info("Exporter configuration updated successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to update exporter config: {e}")
            return False

    def start_exporter(self) -> bool:
        """
        Start the exporter service.

        Returns:
            bool: True if service started successfully, False otherwise
        """
        try:
            logger.info("Enabling and starting exporter service...")

            # Enable service
            subprocess.run(
                ["systemctl", "enable", EXPORTER_SERVICE_NAME],
                check=True,
                capture_output=True,
            )

            # Start service
            subprocess.run(
                ["systemctl", "start", EXPORTER_SERVICE_NAME],
                check=True,
                capture_output=True,
            )

            # Verify service is running
            result = subprocess.run(
                ["systemctl", "is-active", EXPORTER_SERVICE_NAME],
                capture_output=True,
                text=True,
            )

            if result.stdout.strip() == "active":
                logger.info("Exporter service started successfully")
                return True
            else:
                logger.error(f"Exporter service failed to start: {result.stdout}")
                return False

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start exporter service: {e.stderr}")
            return False

    def stop_exporter(self) -> bool:
        """
        Stop the exporter service.

        Returns:
            bool: True if service stopped successfully, False otherwise
        """
        try:
            logger.info("Stopping and disabling exporter service...")

            # Stop service
            subprocess.run(
                ["systemctl", "stop", EXPORTER_SERVICE_NAME],
                check=False,  # Don't fail if already stopped
                capture_output=True,
            )

            # Disable service
            subprocess.run(
                ["systemctl", "disable", EXPORTER_SERVICE_NAME],
                check=False,  # Don't fail if not enabled
                capture_output=True,
            )

            logger.info("Exporter service stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to stop exporter service: {e}")
            return False

    def restart_exporter(self) -> bool:
        """
        Restart the exporter service.

        Returns:
            bool: True if service restarted successfully, False otherwise
        """
        try:
            logger.info("Restarting exporter service...")

            subprocess.run(
                ["systemctl", "restart", EXPORTER_SERVICE_NAME],
                check=True,
                capture_output=True,
            )

            logger.info("Exporter service restarted successfully")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart exporter service: {e.stderr}")
            return False

    def is_exporter_running(self) -> bool:
        """
        Check if exporter service is running.

        Returns:
            bool: True if service is active, False otherwise
        """
        try:
            result = subprocess.run(
                ["systemctl", "is-active", EXPORTER_SERVICE_NAME],
                capture_output=True,
                text=True,
            )
            return result.stdout.strip() == "active"
        except Exception:
            return False

    def is_exporter_installed(self) -> bool:
        """
        Check if exporter is installed.

        Returns:
            bool: True if exporter files exist, False otherwise
        """
        return (
            Path(EXPORTER_SCRIPT_PATH).exists() and Path(EXPORTER_SERVICE_FILE).exists()
        )

    def uninstall_exporter(self) -> bool:
        """
        Uninstall the exporter completely.

        Returns:
            bool: True if uninstallation successful, False otherwise
        """
        try:
            logger.info("Uninstalling exporter...")

            # Stop service first
            self.stop_exporter()

            # Remove files
            for file_path in [
                EXPORTER_SCRIPT_PATH,
                EXPORTER_ENV_FILE,
                EXPORTER_SERVICE_FILE,
            ]:
                if Path(file_path).exists():
                    os.remove(file_path)
                    logger.info(f"Removed {file_path}")

            # Reload systemd
            subprocess.run(["systemctl", "daemon-reload"], check=True)

            logger.info("Exporter uninstalled successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to uninstall exporter: {e}")
            return False
