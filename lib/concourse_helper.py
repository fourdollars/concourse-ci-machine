#!/usr/bin/env python3
"""
Concourse Helper Library - Utilities for charm operations
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Any
from urllib.parse import urlparse

logger = logging.getLogger()

# Installation and configuration paths
CONCOURSE_INSTALL_DIR = "/opt/concourse"
CONCOURSE_DATA_DIR = "/var/lib/concourse"
CONCOURSE_CONFIG_FILE = f"{CONCOURSE_DATA_DIR}/config.env"
CONCOURSE_BIN = f"{CONCOURSE_INSTALL_DIR}/bin/concourse"
SYSTEMD_SERVICE_DIR = "/etc/systemd/system"


class ConcourseHelper:
    """Helper class for Concourse charm operations"""

    def __init__(self, charm):
        self.charm = charm
        self.model = charm.model
        self.config = charm.model.config

    def ensure_directories(self):
        """Ensure required directories exist"""
        dirs = [CONCOURSE_INSTALL_DIR, CONCOURSE_DATA_DIR, f"{CONCOURSE_DATA_DIR}/keys"]
        for dir_path in dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
            os.chmod(dir_path, 0o755)

    def generate_keys(self):
        """Generate Concourse TSA and session signing keys"""
        keys_dir = Path(CONCOURSE_DATA_DIR) / "keys"
        tsa_host_key = keys_dir / "tsa_host_key"
        session_signing_key = keys_dir / "session_signing_key"

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

    def get_concourse_version(self) -> str:
        """Get configured or latest Concourse version"""
        configured = self.config.get("concourse-version")
        if configured:
            return configured

        # Fetch latest version from GitHub releases API
        import urllib.request
        import json

        try:
            url = "https://api.github.com/repos/concourse/concourse/releases/latest"
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode())
                version = data["tag_name"].lstrip("v")
                self.charm.unit.status = MaintenanceStatus(
                    f"Detected latest version: {version}"
                )
                return version
        except Exception as e:
            raise Exception(f"Failed to fetch latest Concourse version: {e}")

    def download_concourse(self, version: str) -> str:
        """Download Concourse binaries for specified version"""
        import urllib.request
        import tarfile
        from ops.model import MaintenanceStatus

        url = f"https://github.com/concourse/concourse/releases/download/v{version}/concourse-{version}-linux-amd64.tgz"
        logger.info(f"Downloading Concourse {version} from {url}")

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
                            self.charm.unit.status = MaintenanceStatus(
                                f"Downloading Concourse {version}... {pct}%"
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
                    raise RuntimeError(
                        f"Downloaded file is empty or missing: {tar_file}"
                    )

                # Extract to installation directory, stripping the top-level 'concourse' directory
                self.charm.unit.status = MaintenanceStatus(
                    f"Extracting Concourse {version}..."
                )
                try:
                    import shutil

                    with tarfile.open(tar_file, "r:gz") as tar:
                        # Extract each member, stripping the first path component
                        for member in tar.getmembers():
                            # Skip if path doesn't start with 'concourse/'
                            if not member.name.startswith("concourse/"):
                                continue
                            # Strip 'concourse/' prefix
                            member.name = member.name[len("concourse/") :]
                            if member.name:  # Skip if it was just 'concourse/' itself
                                tar.extract(member, CONCOURSE_INSTALL_DIR)
                except tarfile.TarError as e:
                    logger.error(f"Failed to extract tarball: {e}")
                    raise

                logger.info(f"Concourse {version} installed successfully")
                return version
        except Exception as e:
            logger.error(f"Concourse download/install failed: {e}")
            raise

    def update_concourse_config(self, db_url: Optional[str] = None):
        """Update Concourse configuration file"""
        keys_dir = Path(CONCOURSE_DATA_DIR) / "keys"
        config = {
            "CONCOURSE_PORT": str(self.config.get("web-port", 8080)),
            "CONCOURSE_WORKER_PROCS": str(self.config.get("worker-procs", 1)),
            "CONCOURSE_LOG_LEVEL": self.config.get("log-level", "info"),
            "CONCOURSE_ENABLE_METRICS": str(
                self.config.get("enable-metrics", True)
            ).lower(),
            "CONCOURSE_MAX_CONCURRENT_DOWNLOADS": str(
                self.config.get("max-concurrent-downloads", 10)
            ),
            "CONCOURSE_CONTAINER_PLACEMENT_STRATEGY": self.config.get(
                "container-placement-strategy", "volume-locality"
            ),
            "CONCOURSE_TSA_HOST_KEY": str(keys_dir / "tsa_host_key"),
            "CONCOURSE_TSA_AUTHORIZED_KEYS": str(keys_dir / "tsa_host_key.pub"),
            "CONCOURSE_SESSION_SIGNING_KEY": str(keys_dir / "session_signing_key"),
        }

        if db_url:
            config["CONCOURSE_POSTGRES_HOST"] = self._parse_pg_host(db_url)
            config["CONCOURSE_POSTGRES_PORT"] = str(self._parse_pg_port(db_url))
            config["CONCOURSE_POSTGRES_USER"] = self._parse_pg_user(db_url)
            config["CONCOURSE_POSTGRES_PASSWORD"] = self._parse_pg_password(db_url)
            config["CONCOURSE_POSTGRES_DATABASE"] = self._parse_pg_dbname(db_url)

        external_url = self.config.get("external-url")
        if external_url:
            config["CONCOURSE_EXTERNAL_URL"] = external_url
        else:
            # Construct default external URL
            # For machine charms, get IP from hostname command as fallback
            import socket

            unit_ip = socket.gethostbyname(socket.gethostname())
            web_port = self.config.get("web-port", 8080)
            config["CONCOURSE_EXTERNAL_URL"] = f"http://{unit_ip}:{web_port}"

        # Write config file
        config_content = "\n".join([f"{k}={v}" for k, v in config.items()])
        Path(CONCOURSE_CONFIG_FILE).write_text(config_content)
        os.chmod(CONCOURSE_CONFIG_FILE, 0o600)

        logger.info(f"Configuration written to {CONCOURSE_CONFIG_FILE}")

    def setup_systemd_services(self):
        """Create systemd service files for Concourse"""
        # Concourse server service
        server_service = f"""[Unit]
Description=Concourse CI Server
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

        # Concourse worker service
        worker_service = f"""[Unit]
Description=Concourse CI Worker
After=concourse-server.service
Requires=concourse-server.service

[Service]
Type=simple
User=concourse
Group=concourse
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
            # Write service files
            server_path = Path(SYSTEMD_SERVICE_DIR) / "concourse-server.service"
            worker_path = Path(SYSTEMD_SERVICE_DIR) / "concourse-worker.service"

            server_path.write_text(server_service)
            worker_path.write_text(worker_service)

            os.chmod(server_path, 0o644)
            os.chmod(worker_path, 0o644)

            # Reload systemd to recognize new service files
            subprocess.run(["systemctl", "daemon-reload"], check=True)

            logger.info(f"Systemd service files written to {SYSTEMD_SERVICE_DIR}")
        except Exception as e:
            logger.error(f"Failed to write systemd files: {e}")
            raise

        # Create concourse user
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

        # Set permissions
        try:
            Path(CONCOURSE_DATA_DIR).chmod(0o755)
            result = subprocess.run(
                ["chown", "-R", "concourse:concourse", CONCOURSE_DATA_DIR],
                capture_output=True,
                check=True,
            )
            logger.info("Directory permissions set")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to set directory permissions: {e}")
            raise

        # Reload systemd
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            logger.info("Systemd daemon reloaded")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to reload systemd: {e}")
            raise

    def start_services(self):
        """Start Concourse systemd services"""
        try:
            for service in ["concourse-server", "concourse-worker"]:
                subprocess.run(
                    ["systemctl", "enable", f"{service}.service"], check=True
                )
                subprocess.run(["systemctl", "start", f"{service}.service"], check=True)
                logger.info(f"Service {service} started")
            logger.info("All services started successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start services: {e}")
            raise

    def stop_services(self):
        """Stop Concourse systemd services"""
        try:
            for service in ["concourse-worker", "concourse-server"]:
                subprocess.run(
                    ["systemctl", "stop", f"{service}.service"], capture_output=True
                )
                logger.info(f"Service {service} stopped")
            logger.info("All services stopped")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to stop services: {e}")
            raise

    def restart_services(self, graceful: bool = True):
        """Restart Concourse services"""
        try:
            if graceful:
                # Graceful restart - restart in order
                subprocess.run(["systemctl", "restart", "concourse-server"], check=True)
                subprocess.run(["systemctl", "restart", "concourse-worker"], check=True)
                logger.info("Services restarted gracefully")
            else:
                # Force restart
                subprocess.run(
                    ["systemctl", "kill", "-9", "concourse-server"], capture_output=True
                )
                subprocess.run(
                    ["systemctl", "kill", "-9", "concourse-worker"], capture_output=True
                )
                self.start_services()
                logger.info("Services force restarted")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart services: {e}")
            raise

    def run_migrations(self):
        """Run database migrations"""
        try:
            logger.info("Running database migrations...")
            result = subprocess.run(
                [CONCOURSE_BIN, "migrate", "--config-from-file", CONCOURSE_CONFIG_FILE],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                if (
                    "already applied" in result.stderr
                    or "already applied" in result.stdout
                ):
                    logger.info("Migrations already applied")
                    return
                logger.warning(f"Migration output: {result.stdout}")
                logger.warning(f"Migration errors: {result.stderr}")
                if result.returncode not in [0, 1]:  # Some warnings are ok
                    raise RuntimeError(
                        f"Migration failed with code {result.returncode}"
                    )
            logger.info("Database migrations completed successfully")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise

    def get_service_status(self) -> Dict[str, Any]:
        """Get current status of services"""
        status = {
            "server_running": False,
            "worker_running": False,
            "db_connected": False,
            "details": {},
        }

        try:
            # Check server status
            result = subprocess.run(
                ["systemctl", "is-active", "concourse-server.service"],
                capture_output=True,
            )
            status["server_running"] = result.returncode == 0

            # Check worker status
            result = subprocess.run(
                ["systemctl", "is-active", "concourse-worker.service"],
                capture_output=True,
            )
            status["worker_running"] = result.returncode == 0

            # Check database connection (basic check)
            try:
                if Path(CONCOURSE_CONFIG_FILE).exists():
                    with open(CONCOURSE_CONFIG_FILE, "r") as f:
                        config = f.read()
                        status["db_connected"] = "CONCOURSE_POSTGRES_HOST" in config

                        # Add detailed info
                        status["details"] = {
                            "config_file_exists": True,
                            "web_port": self._extract_from_config(
                                config, "CONCOURSE_PORT"
                            ),
                            "worker_procs": self._extract_from_config(
                                config, "CONCOURSE_WORKER_PROCS"
                            ),
                        }
                else:
                    status["details"]["config_file_exists"] = False
            except Exception as e:
                logger.debug(f"Could not read config file: {e}")

            logger.info(
                f"Service status: server={status['server_running']}, "
                f"worker={status['worker_running']}, "
                f"db={status['db_connected']}"
            )
        except Exception as e:
            logger.error(f"Status check failed: {e}")

        return status

    @staticmethod
    def _extract_from_config(config_content: str, key: str) -> str:
        """Extract value from config file"""
        for line in config_content.split("\n"):
            if line.startswith(key):
                return line.split("=", 1)[1] if "=" in line else "unknown"
        return "not set"

    def update_database_config(self, db_url: str):
        """Update database configuration from PostgreSQL relation"""
        self.update_concourse_config(db_url)

    def update_concourse_version(self, version: str):
        """Update Concourse to a new version"""
        self.stop_services()
        self.download_concourse(version)
        self.setup_systemd_services()
        self.start_services()
        logger.info(f"Concourse upgraded to {version}")

    def get_admin_password(self) -> str:
        """Get or generate initial admin password"""
        # In production, this should be stored securely in Juju secret or config
        # For now, return a placeholder
        return "change-me-immediately"

    @staticmethod
    def _parse_pg_host(db_url: str) -> str:
        """Parse PostgreSQL host from URL"""
        parsed = urlparse(db_url)
        return parsed.hostname or "localhost"

    @staticmethod
    def _parse_pg_port(db_url: str) -> int:
        """Parse PostgreSQL port from URL"""
        parsed = urlparse(db_url)
        return parsed.port or 5432

    @staticmethod
    def _parse_pg_user(db_url: str) -> str:
        """Parse PostgreSQL user from URL"""
        parsed = urlparse(db_url)
        return parsed.username or "postgres"

    @staticmethod
    def _parse_pg_password(db_url: str) -> str:
        """Parse PostgreSQL password from URL"""
        parsed = urlparse(db_url)
        return parsed.password or ""

    @staticmethod
    def _parse_pg_dbname(db_url: str) -> str:
        """Parse PostgreSQL database name from URL"""
        parsed = urlparse(db_url)
        path = parsed.path.lstrip("/")
        return path.split("?")[0] or "concourse"


# Module-level functions for backward compatibility


def ensure_concourse_installed(helper: ConcourseHelper):
    """Ensure Concourse is installed and ready"""
    helper.ensure_directories()
    version = helper.get_concourse_version()
    helper.download_concourse(version)
    helper.generate_keys()
    helper.setup_systemd_services()

    # Create /etc/default/concourse file (can be empty, just needs to exist)
    Path("/etc/default/concourse").touch()
    os.chmod("/etc/default/concourse", 0o644)

    logger.info("Concourse installation completed")


def setup_systemd_services(helper: ConcourseHelper):
    """Setup systemd services"""
    helper.setup_systemd_services()


def update_concourse_config(helper: ConcourseHelper):
    """Update Concourse configuration"""
    helper.update_concourse_config()


def get_postgresql_url(relation) -> Optional[str]:
    """Extract PostgreSQL URL from relation data"""
    if not relation or not relation.data:
        return None

    for unit, data in relation.data.items():
        # Standard PostgreSQL charm relation data
        if "host" in data and "user" in data:
            host = data.get("host", "localhost")
            port = data.get("port", "5432")
            user = data.get("user", "postgres")
            password = data.get("password", "")
            dbname = data.get("database", "concourse")

            url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
            return url

    return None
