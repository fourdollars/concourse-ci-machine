#!/usr/bin/env python3
"""
Concourse Web Server Helper Library
"""

import logging
import os
import subprocess
from pathlib import Path
from ops.model import MaintenanceStatus

from concourse_common import (
    CONCOURSE_BIN,
    CONCOURSE_CONFIG_FILE,
    CONCOURSE_DATA_DIR,
    SYSTEMD_SERVICE_DIR,
    KEYS_DIR,
)

logger = logging.getLogger(__name__)


class ConcourseWebHelper:
    """Helper class for Concourse web server operations"""

    def __init__(self, charm):
        self.charm = charm
        self.model = charm.model
        self.config = charm.model.config

    def setup_systemd_service(self):
        """Create systemd service file for Concourse web server"""
        server_service = f"""[Unit]
Description=Concourse CI Web Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=concourse
Group=concourse
WorkingDirectory={CONCOURSE_DATA_DIR}
EnvironmentFile={CONCOURSE_CONFIG_FILE}
EnvironmentFile=/etc/default/concourse
ExecStart={CONCOURSE_BIN} web
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

        try:
            server_path = Path(SYSTEMD_SERVICE_DIR) / "concourse-server.service"
            server_path.write_text(server_service)
            os.chmod(server_path, 0o644)

            # Reload systemd to recognize new service files
            subprocess.run(["systemctl", "daemon-reload"], check=True)

            logger.info(f"Web server systemd service created")
        except Exception as e:
            logger.error(f"Failed to create systemd service: {e}")
            raise

    def update_config(self, db_url: str = None, admin_password: str = "admin"):
        """Update Concourse web server configuration"""
        import socket

        keys_dir = Path(KEYS_DIR)
        username = self.config.get("initial-admin-username", "admin")
        config = {
            "CONCOURSE_PORT": str(self.config.get("web-port", 8080)),
            "CONCOURSE_LOG_LEVEL": self.config.get("log-level", "info"),
            "CONCOURSE_ENABLE_METRICS": str(
                self.config.get("enable-metrics", True)
            ).lower(),
            "CONCOURSE_TSA_HOST_KEY": str(keys_dir / "tsa_host_key"),
            "CONCOURSE_TSA_AUTHORIZED_KEYS": str(keys_dir / "authorized_worker_keys"),
            "CONCOURSE_SESSION_SIGNING_KEY": str(keys_dir / "session_signing_key"),
            "CONCOURSE_TSA_PUBLIC_KEY": str(keys_dir / "tsa_host_key.pub"),
            "CONCOURSE_ADD_LOCAL_USER": f"{username}:{admin_password}",
            "CONCOURSE_MAIN_TEAM_LOCAL_USER": username,
        }

        # Add database configuration
        if db_url:
            from urllib.parse import urlparse

            parsed = urlparse(db_url)
            config["CONCOURSE_POSTGRES_HOST"] = parsed.hostname or "localhost"
            config["CONCOURSE_POSTGRES_PORT"] = str(parsed.port or 5432)
            config["CONCOURSE_POSTGRES_USER"] = parsed.username or "postgres"
            config["CONCOURSE_POSTGRES_PASSWORD"] = parsed.password or ""
            config["CONCOURSE_POSTGRES_DATABASE"] = (
                parsed.path.lstrip("/") or "concourse"
            )

        # Set external URL
        external_url = self.config.get("external-url")
        if external_url:
            config["CONCOURSE_EXTERNAL_URL"] = external_url
        else:
            unit_ip = socket.gethostbyname(socket.gethostname())
            web_port = self.config.get("web-port", 8080)
            config["CONCOURSE_EXTERNAL_URL"] = f"http://{unit_ip}:{web_port}"

        # Write config file
        self._write_config(config)
        logger.info("Web server configuration updated")

    def _write_config(self, config: dict):
        """Write configuration to file"""
        try:
            config_lines = [f"{k}={v}" for k, v in config.items()]
            Path(CONCOURSE_CONFIG_FILE).write_text("\n".join(config_lines) + "\n")
            os.chmod(CONCOURSE_CONFIG_FILE, 0o640)
            subprocess.run(
                ["chown", "root:concourse", CONCOURSE_CONFIG_FILE],
                check=True,
                capture_output=True,
            )
            logger.info(f"Configuration written to {CONCOURSE_CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Failed to write config: {e}")
            raise

    def start_service(self):
        """Start Concourse web server service"""
        try:
            subprocess.run(["systemctl", "enable", "concourse-server"], check=True)
            subprocess.run(["systemctl", "start", "concourse-server"], check=True)
            logger.info("Web server service started")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start web server: {e}")
            raise

    def stop_service(self):
        """Stop Concourse web server service"""
        try:
            subprocess.run(
                ["systemctl", "stop", "concourse-server"], capture_output=True
            )
            subprocess.run(
                ["systemctl", "disable", "concourse-server"], capture_output=True
            )
            logger.info("Web server service stopped")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop web server: {e}")

    def restart_service(self):
        """Restart Concourse web server service"""
        try:
            subprocess.run(["systemctl", "restart", "concourse-server"], check=True)
            logger.info("Web server service restarted")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart web server: {e}")
            raise

    def is_running(self) -> bool:
        """Check if web server is running"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "concourse-server"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and result.stdout.strip() == "active"
        except:
            return False
