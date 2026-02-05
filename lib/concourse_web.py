#!/usr/bin/env python3
"""
Concourse Web Server Helper Library
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from concourse_common import (
    CONCOURSE_BIN,
    CONCOURSE_CONFIG_FILE,
    CONCOURSE_DATA_DIR,
    SYSTEMD_SERVICE_DIR,
    KEYS_DIR,
    get_filesystem_id,
)

logger = logging.getLogger(__name__)

# Import storage coordinator (may not be available)
try:
    from storage_coordinator import (
        SharedStorage,
        LockCoordinator,
        StorageCoordinator,
    )

    HAS_STORAGE_COORDINATOR = True
except ImportError:
    HAS_STORAGE_COORDINATOR = False
    logger.warning("storage_coordinator not available")


class ConcourseWebHelper:
    """Helper class for Concourse web server operations"""

    def __init__(self, charm):
        self.charm = charm
        self.model = charm.model
        self.config = charm.model.config
        self.storage_coordinator = (
            None  # Will be initialized if shared storage available
        )

    def initialize_shared_storage(self) -> Optional[object]:
        """Initialize shared storage for web/leader unit (T022).

        Returns:
            StorageCoordinator instance if shared storage is available, None otherwise
        """
        if not HAS_STORAGE_COORDINATOR:
            logger.info("Storage coordinator not available, skipping shared storage")
            return None

        # Check shared-storage config
        shared_storage_mode = self.charm.config.get("shared-storage", "none")

        try:
            if shared_storage_mode == "none":
                logger.info("Shared storage disabled (shared-storage=none)")
                return None

            # For LXC mode, always use /var/lib/concourse
            if shared_storage_mode == "lxc":
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
            logger.info(f"  - Bin directory: {shared_storage.bin_directory}")
            logger.info(f"  - Keys directory: {shared_storage.keys_directory}")

            # Initialize LockCoordinator
            lock_coordinator = LockCoordinator(
                lock_path=shared_storage.lock_file_path,
                holder_unit=self.charm.unit.name,
                timeout_seconds=600,  # 10 minutes
            )

            # Initialize StorageCoordinator (web/leader downloads)
            self.storage_coordinator = StorageCoordinator(
                storage=shared_storage,
                lock=lock_coordinator,
                is_leader=True,  # Web units act as downloaders
            )

            logger.info("Storage coordinator initialized for web/leader unit")
            return self.storage_coordinator

        except Exception as e:
            logger.error(f"Failed to initialize shared storage: {e}")
            # If shared storage is configured but failed to init, we should probably know why
            if shared_storage_mode != "none":
                logger.error("Raising exception because shared-storage is enabled")
                raise
            # Non-fatal: fall back to local installation
            return None

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
EnvironmentFile=-{CONCOURSE_CONFIG_FILE}
EnvironmentFile=-/etc/default/concourse
ExecStart={CONCOURSE_BIN} web
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
AmbientCapabilities=CAP_NET_BIND_SERVICE

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

            server_path = Path(SYSTEMD_SERVICE_DIR) / "concourse-server.service"
            server_path.write_text(server_service)
            os.chmod(server_path, 0o644)

            # Reload systemd to recognize new service files
            subprocess.run(["systemctl", "daemon-reload"], check=True)

            logger.info("Web server systemd service created")
        except Exception as e:
            logger.error(f"Failed to create systemd service: {e}")
            raise

    def update_config(self, db_url: str = None, admin_password: str = "admin"):
        """Update Concourse web server configuration"""
        import socket

        keys_dir = Path(KEYS_DIR)
        username = self.config.get("initial-admin-username", "admin")
        config = {
            "CONCOURSE_BIND_PORT": str(self.config.get("web-port", 8080)),
            "CONCOURSE_LOG_LEVEL": self.config.get("log-level", "info"),
            "CONCOURSE_TSA_HOST_KEY": str(keys_dir / "tsa_host_key"),
            "CONCOURSE_TSA_AUTHORIZED_KEYS": str(keys_dir / "authorized_worker_keys"),
            "CONCOURSE_SESSION_SIGNING_KEY": str(keys_dir / "session_signing_key"),
            "CONCOURSE_TSA_PUBLIC_KEY": str(keys_dir / "tsa_host_key.pub"),
            "CONCOURSE_ADD_LOCAL_USER": f"{username}:{admin_password}",
            "CONCOURSE_MAIN_TEAM_LOCAL_USER": username,
        }

        # Configure Prometheus metrics endpoint (binds to 0.0.0.0:9391 when enabled)
        if self.config.get("enable-metrics", True):
            config["CONCOURSE_PROMETHEUS_BIND_IP"] = "0.0.0.0"
            config["CONCOURSE_PROMETHEUS_BIND_PORT"] = "9391"

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

        # Add Vault configuration if vault-url is set
        if self.config.get("vault-url"):
            logger.info("Vault URL is set, enabling Vault credential manager")
            config["CONCOURSE_VAULT_URL"] = self.config["vault-url"]
            if self.config.get("vault-auth-backend"):
                config["CONCOURSE_VAULT_AUTH_BACKEND"] = self.config[
                    "vault-auth-backend"
                ]
            if self.config.get("vault-auth-backend-max-ttl"):
                config["CONCOURSE_VAULT_AUTH_BACKEND_MAX_TTL"] = self.config[
                    "vault-auth-backend-max-ttl"
                ]
            if self.config.get("vault-auth-param"):
                config["CONCOURSE_VAULT_AUTH_PARAM"] = self.config["vault-auth-param"]
            if self.config.get("vault-ca-cert"):
                config["CONCOURSE_VAULT_CA_CERT"] = self.config["vault-ca-cert"]
            if self.config.get("vault-client-cert"):
                config["CONCOURSE_VAULT_CLIENT_CERT"] = self.config["vault-client-cert"]
            if self.config.get("vault-client-key"):
                config["CONCOURSE_VAULT_CLIENT_KEY"] = self.config["vault-client-key"]
            if self.config.get("vault-client-token"):
                config["CONCOURSE_VAULT_CLIENT_TOKEN"] = self.config[
                    "vault-client-token"
                ]
            if self.config.get("vault-lookup-templates"):
                config["CONCOURSE_VAULT_LOOKUP_TEMPLATES"] = self.config[
                    "vault-lookup-templates"
                ]
            if self.config.get("vault-namespace"):
                config["CONCOURSE_VAULT_NAMESPACE"] = self.config["vault-namespace"]
            if self.config.get("vault-path-prefix"):
                config["CONCOURSE_VAULT_PATH_PREFIX"] = self.config["vault-path-prefix"]
            if self.config.get("vault-shared-path"):
                config["CONCOURSE_VAULT_SHARED_PATH"] = self.config["vault-shared-path"]

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
            subprocess.run(
                ["systemctl", "enable", "concourse-server.service"], check=True
            )
            subprocess.run(
                ["systemctl", "start", "concourse-server.service"], check=True
            )
            logger.info("Web server service started")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start web server: {e}")
            raise

    def stop_service(self):
        """Stop Concourse web server service"""
        try:
            subprocess.run(
                ["systemctl", "stop", "concourse-server.service"], capture_output=True
            )
            subprocess.run(
                ["systemctl", "disable", "concourse-server.service"],
                capture_output=True,
            )
            logger.info("Web server service stopped")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop web server: {e}")

    def restart_service(self):
        """Restart Concourse web server service"""
        try:
            subprocess.run(
                ["systemctl", "restart", "concourse-server.service"], check=True
            )
            logger.info("Web server service restarted")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart web server: {e}")
            raise

    def is_running(self) -> bool:
        """Check if web server is running"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "concourse-server.service"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and result.stdout.strip() == "active"
        except Exception:
            return False

    def upgrade_with_shared_storage(self, target_version: str) -> None:
        """Perform coordinated upgrade with shared storage (T050).

        Steps:
        1. Acquire exclusive lock
        2. Download new binaries to shared storage
        3. Update version marker
        4. Restart web server service

        Args:
            target_version: Target Concourse version (e.g., "7.14.3")

        Raises:
            LockAcquireError: If another unit holds the download lock
            Exception: If download or installation fails
        """
        if not self.storage_coordinator:
            raise Exception("Storage coordinator not initialized for upgrade")

        logger.info(f"Starting coordinated upgrade to v{target_version}")

        # Step 1: Acquire exclusive lock (T050)
        logger.info("Acquiring exclusive lock for binary download")
        with self.storage_coordinator.lock.acquire_exclusive():
            logger.info("Lock acquired, downloading binaries")

            # Step 2: Download new version to shared storage (T050)
            from concourse_installer import download_and_install_concourse_with_storage

            download_and_install_concourse_with_storage(
                self.charm, target_version, self.storage_coordinator
            )

            # Step 3: Update version marker (T050)
            logger.info(f"Writing version marker: {target_version}")
            self.storage_coordinator.storage.write_installed_version(target_version)

        logger.info("Lock released, binaries installed")

        # Step 4: Restart web server (T050)
        logger.info("Restarting web server with new binaries")
        self.restart_service()
        logger.info(f"Web server upgraded to v{target_version} successfully")
