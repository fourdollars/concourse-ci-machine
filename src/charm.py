#!/usr/bin/env python3
"""
Concourse CI Juju Charm - Main operator code
Supports web-only, worker-only, or combined deployments
"""

import logging
import secrets
import string
import sys
from pathlib import Path

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from ops.charm import CharmBase
from ops.main import main
from ops.model import (
    ActiveStatus,
    WaitingStatus,
    BlockedStatus,
    MaintenanceStatus,
)

# Import new modular helpers
from concourse_common import (
    ensure_directories,
    generate_keys,
    create_concourse_user,
    get_concourse_version,
    KEYS_DIR,
)
from concourse_installer import (
    download_and_install_concourse,
    verify_installation,
)
from concourse_web import ConcourseWebHelper
from concourse_worker import ConcourseWorkerHelper

# Configure logging
log_handlers = [logging.StreamHandler()]
log_file_path = Path("/var/log/concourse-ci.log")
if log_file_path.parent.exists() and log_file_path.parent.is_dir():
    try:
        log_handlers.append(logging.FileHandler(log_file_path))
    except (PermissionError, OSError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("concourse-ci")


class ConcourseCharm(CharmBase):
    """Main Concourse CI charm class with web/worker role support"""

    def __init__(self, *args):
        super().__init__(*args)
        self.web_helper = ConcourseWebHelper(self)
        self.worker_helper = ConcourseWorkerHelper(self)

        # Register event handlers
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.stop, self._on_stop)

        # PostgreSQL relation
        self.framework.observe(
            self.on.postgresql_relation_created, self._on_postgresql_relation_created
        )
        self.framework.observe(
            self.on.postgresql_relation_changed, self._on_postgresql_relation_changed
        )
        self.framework.observe(
            self.on.postgresql_relation_broken, self._on_postgresql_relation_broken
        )

        # Peer relation
        self.framework.observe(
            self.on.concourse_peer_relation_changed, self._on_peer_relation_changed
        )

        # Actions
        self.framework.observe(
            self.on.get_admin_password_action, self._on_get_admin_password_action
        )

    def _get_deployment_mode(self) -> str:
        """
        Determine deployment mode for this unit

        Returns:
            'web', 'worker', or 'both'
        """
        config_mode = self.config.get("deployment-mode", "auto")

        if config_mode == "web":
            return "web"
        elif config_mode == "worker":
            return "worker"
        elif config_mode == "all":
            return "both"
        elif config_mode == "auto":
            # Auto mode: leader runs web, non-leaders run workers
            if self.unit.is_leader():
                return "web"
            else:
                return "worker"
        else:
            logger.warning(
                f"Unknown deployment-mode: {config_mode}, defaulting to 'both'"
            )
            return "both"

    def _should_run_web(self) -> bool:
        """Check if this unit should run web server"""
        mode = self._get_deployment_mode()
        return mode in ("web", "both")

    def _should_run_worker(self) -> bool:
        """Check if this unit should run worker"""
        mode = self._get_deployment_mode()
        return mode in ("worker", "both")

    def _generate_random_password(self, length: int = 24) -> str:
        """Generate a secure random password"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        return password

    def _get_or_create_admin_password(self) -> str:
        """Get existing admin password from peer data or generate new one"""
        # Only leader manages the password
        if not self.unit.is_leader():
            # Non-leaders read from peer data
            peer_relation = self.model.get_relation("concourse-peer")
            if peer_relation:
                password = peer_relation.data[self.app].get("admin-password")
                if password:
                    return password
            return "admin"  # Fallback for non-leaders without peer data

        # Leader: check if password already exists in peer data
        peer_relation = self.model.get_relation("concourse-peer")
        if peer_relation:
            password = peer_relation.data[self.app].get("admin-password")
            if password:
                logger.info("Using existing admin password from peer data")
                return password

        # Generate new password
        password = self._generate_random_password()
        logger.info("Generated new admin password")

        # Store in peer relation data
        if peer_relation:
            peer_relation.data[self.app]["admin-password"] = password
            logger.info("Stored admin password in peer data")

        return password

    def _on_install(self, event):
        """Handle install event"""
        try:
            self.unit.status = MaintenanceStatus("Installing Concourse CI...")
            logger.info(
                f"Starting Concourse installation (mode: {self._get_deployment_mode()})"
            )

            # Common setup
            ensure_directories()
            create_concourse_user()

            # Download and install Concourse
            version = get_concourse_version(self.config)
            download_and_install_concourse(self, version)

            # Generate keys
            generate_keys()

            # Create /etc/default/concourse
            Path("/etc/default/concourse").touch()
            import os

            os.chmod("/etc/default/concourse", 0o644)

            # Setup services based on role
            if self._should_run_web():
                logger.info("Setting up web server service")
                self.web_helper.setup_systemd_service()

            if self._should_run_worker():
                logger.info("Setting up worker service")
                self.worker_helper.setup_systemd_service()
                # Install containerd for worker
                import subprocess

                subprocess.run(["apt-get", "update", "-qq"], capture_output=True)
                subprocess.run(
                    ["apt-get", "install", "-y", "containerd"], capture_output=True
                )

            self.unit.status = MaintenanceStatus("Installation complete")
            logger.info("Concourse installation completed successfully")

        except Exception as e:
            logger.error(f"Installation failed: {e}", exc_info=True)
            self.unit.status = BlockedStatus(f"Installation failed: {e}")

    def _on_config_changed(self, event):
        """Handle config-changed event"""
        try:
            logger.info("Config changed event triggered")
            mode = self._get_deployment_mode()
            logger.info(f"Deployment mode: {mode}")

            # Update configuration based on role
            if mode == "both":
                # When running both, we need to merge configs
                logger.info("Updating merged web+worker configuration")
                self._update_merged_config()
            else:
                # Single role - update separately
                if self._should_run_web():
                    logger.info("Updating web server configuration")
                    db_url = self._get_postgresql_url()
                    admin_password = self._get_or_create_admin_password()
                    self.web_helper.update_config(
                        db_url=db_url, admin_password=admin_password
                    )

                if self._should_run_worker():
                    logger.info("Updating worker configuration")
                    tsa_host = self._get_tsa_host()
                    self.worker_helper.update_config(tsa_host=tsa_host)

            self._update_status()
            logger.info("Configuration updated successfully")

        except Exception as e:
            logger.error(f"Config update failed: {e}", exc_info=True)
            self.unit.status = BlockedStatus(f"Config failed: {e}")

    def _on_start(self, event):
        """Handle start event"""
        try:
            logger.info("Unit starting")

            # Publish keys if we're a web server
            if self._should_run_web():
                self._publish_keys_to_peers()

            self._update_status()
        except Exception as e:
            logger.error(f"Start failed: {e}", exc_info=True)
            self.unit.status = BlockedStatus(f"Start failed: {e}")

    def _on_update_status(self, event):
        """Handle update-status event"""
        self._update_status()

    def _on_stop(self, event):
        """Handle stop event"""
        try:
            logger.info("Stopping services")
            if self._should_run_web():
                self.web_helper.stop_service()
            if self._should_run_worker():
                self.worker_helper.stop_service()
        except Exception as e:
            logger.error(f"Stop failed: {e}", exc_info=True)

    def _on_postgresql_relation_created(self, event):
        """Handle PostgreSQL relation created"""
        logger.info("PostgreSQL relation created")
        if not self._should_run_web():
            logger.info("Not a web unit, skipping PostgreSQL relation")
            return

    def _on_postgresql_relation_changed(self, event):
        """Handle PostgreSQL relation changed"""
        try:
            if not self._should_run_web():
                logger.info("Not a web unit, skipping PostgreSQL configuration")
                return

            logger.info("PostgreSQL relation changed")
            db_url = self._get_postgresql_url()

            if not db_url:
                logger.warning("PostgreSQL URL not yet available")
                self.unit.status = WaitingStatus("Waiting for PostgreSQL database...")
                return

            logger.info("Database configuration updated")
            admin_password = self._get_or_create_admin_password()
            self.web_helper.update_config(db_url=db_url, admin_password=admin_password)

            # Restart web service
            if self.web_helper.is_running():
                self.web_helper.restart_service()
            else:
                self.web_helper.start_service()

            self._update_status()

        except Exception as e:
            logger.error(f"PostgreSQL relation handling failed: {e}", exc_info=True)
            self.unit.status = BlockedStatus(f"Database config failed: {e}")

    def _on_postgresql_relation_broken(self, event):
        """Handle PostgreSQL relation broken"""
        if self._should_run_web():
            logger.warning("PostgreSQL relation broken")
            self.web_helper.stop_service()
            self.unit.status = BlockedStatus("PostgreSQL database required")

    def _publish_keys_to_peers(self):
        """Publish TSA keys and web IP to peer relation"""
        from pathlib import Path
        import socket

        peer_relation = self.model.get_relation("concourse-peer")
        if not peer_relation:
            logger.info("No peer relation found, skipping key publishing")
            return

        try:
            # Get IPv4 address
            import subprocess

            result = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
            ips = result.stdout.strip().split()
            # Find first IPv4 address
            unit_ip = None
            for ip in ips:
                if "." in ip and not ip.startswith("127."):
                    unit_ip = ip
                    break

            if not unit_ip:
                unit_ip = socket.gethostbyname(socket.gethostname())

            peer_relation.data[self.unit]["web-ip"] = unit_ip
            logger.info(f"Published web IP: {unit_ip}")

            # Publish TSA keys
            keys_dir = Path(KEYS_DIR)
            tsa_pub_key_path = keys_dir / "tsa_host_key.pub"

            if tsa_pub_key_path.exists():
                tsa_pub_key = tsa_pub_key_path.read_text().strip()
                peer_relation.data[self.unit]["tsa-public-key"] = tsa_pub_key
                logger.info("Published TSA public key to peers")
            else:
                logger.warning(f"TSA public key not found at {tsa_pub_key_path}")

        except Exception as e:
            logger.error(f"Failed to publish keys to peers: {e}", exc_info=True)

    def _authorize_worker_key(self, worker_pub_key: str):
        """Add a worker's public key to authorized_worker_keys"""
        from pathlib import Path

        try:
            keys_dir = Path(KEYS_DIR)
            auth_keys_file = keys_dir / "authorized_worker_keys"

            # Read existing keys
            existing_keys = set()
            if auth_keys_file.exists():
                existing_keys = set(auth_keys_file.read_text().strip().split("\n"))

            # Add new key if not already present
            if worker_pub_key and worker_pub_key not in existing_keys:
                with open(auth_keys_file, "a") as f:
                    f.write(worker_pub_key + "\n")
                logger.info("Added worker public key to authorized_worker_keys")

                # Restart web server to pick up new key
                if self.web_helper.is_running():
                    self.web_helper.restart_service()
                    logger.info("Restarted web server to apply new worker key")
            else:
                logger.debug("Worker key already authorized")

        except Exception as e:
            logger.error(f"Failed to authorize worker key: {e}", exc_info=True)

    def _on_peer_relation_changed(self, event):
        """Handle peer relation changed - share TSA keys and worker keys"""
        logger.info("Peer relation changed")

        # Web server: publish keys and authorize workers
        if self._should_run_web():
            self._publish_keys_to_peers()

            # Check for worker keys to authorize
            for unit in event.relation.units:
                data = event.relation.data.get(unit, {})
                worker_pub_key = data.get("worker-public-key")
                if worker_pub_key:
                    logger.info(f"Found worker public key from {unit}")
                    self._authorize_worker_key(worker_pub_key)

        # Worker: publish our key and retrieve TSA configuration
        if self._should_run_worker():
            from pathlib import Path

            # Publish our worker public key
            keys_dir = Path(KEYS_DIR)
            worker_pub_key_path = keys_dir / "worker_key.pub"
            if worker_pub_key_path.exists():
                worker_pub_key = worker_pub_key_path.read_text().strip()
                event.relation.data[self.unit]["worker-public-key"] = worker_pub_key
                logger.info("Published worker public key to peers")

            # Retrieve TSA configuration from web server
            tsa_pub_key = None
            web_ip = None

            for unit in event.relation.units:
                data = event.relation.data.get(unit, {})
                if "tsa-public-key" in data and "web-ip" in data:
                    tsa_pub_key = data.get("tsa-public-key")
                    web_ip = data.get("web-ip")
                    logger.info(f"Retrieved TSA configuration from {unit}")
                    break

            if tsa_pub_key and web_ip:
                # Write TSA public key
                tsa_pub_key_path = keys_dir / "tsa_host_key.pub"
                tsa_pub_key_path.write_text(tsa_pub_key + "\n")
                logger.info("Wrote TSA public key from peer relation")

                # Update worker config with TSA host
                tsa_host = f"{web_ip}:2222"
                self.worker_helper.update_config(tsa_host=tsa_host)
                logger.info(f"Updated worker config with TSA host: {tsa_host}")

                # Restart worker to apply config
                if self.worker_helper.is_running():
                    self.worker_helper.restart_service()
                    logger.info("Restarted worker with new configuration")
                else:
                    self.worker_helper.start_service()
                    logger.info("Started worker with new configuration")
            else:
                logger.info("TSA configuration not yet available in peer relation")

    def _update_merged_config(self):
        """Update config when running both web and worker"""
        from pathlib import Path
        import os
        import subprocess

        # Get configs from both helpers (but don't write them)
        db_url = self._get_postgresql_url()

        # Build merged config manually
        from concourse_common import CONCOURSE_CONFIG_FILE, KEYS_DIR

        keys_dir = Path(KEYS_DIR)
        worker_dir = Path("/var/lib/concourse/worker")
        worker_dir.mkdir(exist_ok=True)

        import socket

        unit_ip = socket.gethostbyname(socket.gethostname())
        web_port = self.config.get("web-port", 8080)

        config = {
            # Web server config
            "CONCOURSE_PORT": str(web_port),
            "CONCOURSE_LOG_LEVEL": self.config.get("log-level", "info"),
            "CONCOURSE_ENABLE_METRICS": str(
                self.config.get("enable-metrics", True)
            ).lower(),
            "CONCOURSE_TSA_HOST_KEY": str(keys_dir / "tsa_host_key"),
            "CONCOURSE_TSA_AUTHORIZED_KEYS": str(keys_dir / "authorized_worker_keys"),
            "CONCOURSE_SESSION_SIGNING_KEY": str(keys_dir / "session_signing_key"),
            "CONCOURSE_TSA_PUBLIC_KEY": str(keys_dir / "tsa_host_key.pub"),
            "CONCOURSE_ADD_LOCAL_USER": f"{self.config.get('initial-admin-username', 'admin')}:{self._get_or_create_admin_password()}",
            "CONCOURSE_MAIN_TEAM_LOCAL_USER": self.config.get(
                "initial-admin-username", "admin"
            ),
            "CONCOURSE_EXTERNAL_URL": self.config.get("external-url")
            or f"http://{unit_ip}:{web_port}",
            # Worker config
            "CONCOURSE_WORKER_PROCS": str(self.config.get("worker-procs", 1)),
            "CONCOURSE_TSA_WORKER_PRIVATE_KEY": str(keys_dir / "worker_key"),
            "CONCOURSE_WORK_DIR": str(worker_dir),
            "CONCOURSE_TSA_HOST": "127.0.0.1:2222",
            "CONCOURSE_RUNTIME": "containerd",
            "CONCOURSE_BAGGAGECLAIM_DRIVER": "naive",
            "CONCOURSE_CONTAINERD_DNS_PROXY_ENABLE": str(
                self.config.get("containerd-dns-proxy-enable", False)
            ).lower(),
            "CONCOURSE_CONTAINERD_DNS_SERVER": self.config.get(
                "containerd-dns-server", "1.1.1.1,8.8.8.8"
            ),
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

        # Write merged config
        config_lines = [f"{k}={v}" for k, v in config.items()]
        Path(CONCOURSE_CONFIG_FILE).write_text("\n".join(config_lines) + "\n")
        os.chmod(CONCOURSE_CONFIG_FILE, 0o640)
        subprocess.run(
            ["chown", "root:concourse", CONCOURSE_CONFIG_FILE],
            check=True,
            capture_output=True,
        )
        logger.info(f"Merged configuration written to {CONCOURSE_CONFIG_FILE}")

    def _get_postgresql_url(self) -> str:
        """Extract PostgreSQL URL from relation data"""
        relation = self.model.get_relation("postgresql")
        if not relation or not relation.data:
            return None

        for unit, data in relation.data.items():
            if hasattr(unit, "name") and "postgresql" in unit.name:
                host = data.get("host")
                port = data.get("port", "5432")
                database = data.get("database")
                user = data.get("user")
                password = data.get("password")

                if all([host, database, user, password]):
                    return f"postgres://{user}:{password}@{host}:{port}/{database}"

        return None

    def _get_tsa_host(self) -> str:
        """Get TSA host address (web server)"""
        mode = self._get_deployment_mode()

        # If running both on same unit, use localhost
        if mode == "both":
            return "127.0.0.1:2222"

        # In auto/worker mode, need to find the leader (web server) IP
        # Try to get leader IP from peer relation
        peer_relation = self.model.get_relation("concourse-peer")
        if peer_relation:
            for unit in peer_relation.units:
                # Check if this unit is the leader by checking relation data
                if unit == self.model.unit:
                    continue
                # Get the unit's binding address
                try:
                    # Try to get from relation data first
                    data = peer_relation.data.get(unit, {})
                    web_ip = data.get("web-ip")
                    if web_ip:
                        return f"{web_ip}:2222"
                except:
                    pass

        # Fallback: if we're the leader, return our own IP
        if self.unit.is_leader():
            import socket

            try:
                unit_ip = socket.gethostbyname(socket.gethostname())
                return f"{unit_ip}:2222"
            except:
                return "127.0.0.1:2222"

        # Last resort: try localhost (will fail for remote workers)
        return "127.0.0.1:2222"

    def _update_status(self):
        """Update unit status based on service states"""
        try:
            mode = self._get_deployment_mode()

            # Check if installation is complete
            if not verify_installation():
                self.unit.status = BlockedStatus("Concourse not installed")
                return

            # Check database for web mode
            if self._should_run_web():
                db_url = self._get_postgresql_url()
                if not db_url:
                    self.unit.status = WaitingStatus(
                        "Waiting for PostgreSQL database..."
                    )
                    return

                # Start web service if not running
                if not self.web_helper.is_running():
                    self.web_helper.start_service()

                if not self.web_helper.is_running():
                    self.unit.status = MaintenanceStatus("Services starting...")
                    return

            # Start worker service if needed
            if self._should_run_worker():
                if not self.worker_helper.is_running():
                    self.worker_helper.start_service()

                if not self.worker_helper.is_running():
                    self.unit.status = MaintenanceStatus("Worker starting...")
                    return

            # All good
            if mode == "web":
                self.unit.status = ActiveStatus("Web server ready")
            elif mode == "worker":
                self.unit.status = ActiveStatus("Worker ready")
            else:
                self.unit.status = ActiveStatus("Ready")

        except Exception as e:
            logger.error(f"Status update failed: {e}", exc_info=True)
            self.unit.status = BlockedStatus(f"Status check failed: {e}")

    def _on_get_admin_password_action(self, event):
        """Handle get-admin-password action"""
        try:
            username = self.config.get("initial-admin-username", "admin")
            password = self._get_or_create_admin_password()

            event.set_results(
                {
                    "username": username,
                    "password": password,
                    "message": "Use these credentials to login to Concourse web UI",
                }
            )
            logger.info("Admin password retrieved via action")
        except Exception as e:
            event.fail(f"Failed to retrieve admin password: {e}")
            logger.error(f"Get admin password action failed: {e}", exc_info=True)


if __name__ == "__main__":
    main(ConcourseCharm)
