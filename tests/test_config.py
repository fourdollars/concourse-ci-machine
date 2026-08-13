#!/usr/bin/env python3
"""Unit tests for config.env merge behavior and new config option mapping."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


# Add lib/ to path so we can import the helpers
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


# ---------------------------------------------------------------------------
# _read_config / _write_config (merge + sort) — tests for both Web and Worker
# ---------------------------------------------------------------------------


class TestReadConfig:
    """Tests for the _read_config static method."""

    def test_read_empty_file(self, tmp_path):
        """Reading an empty file returns an empty dict."""
        config_file = tmp_path / "config.env"
        config_file.write_text("")
        from concourse_web import ConcourseWebHelper

        result = ConcourseWebHelper._read_config(str(config_file))
        assert result == {}

    def test_read_nonexistent_file(self, tmp_path):
        """Reading a nonexistent file returns an empty dict."""
        from concourse_web import ConcourseWebHelper

        result = ConcourseWebHelper._read_config(str(tmp_path / "missing.env"))
        assert result == {}

    def test_read_key_value_pairs(self, tmp_path):
        """Reads standard KEY=VALUE lines."""
        config_file = tmp_path / "config.env"
        config_file.write_text("A=foo\nB=bar\nC=baz\n")
        from concourse_web import ConcourseWebHelper

        result = ConcourseWebHelper._read_config(str(config_file))
        assert result == {"A": "foo", "B": "bar", "C": "baz"}

    def test_read_preserves_values_with_equals(self, tmp_path):
        """Values containing '=' are preserved (only first '=' splits key/value)."""
        config_file = tmp_path / "config.env"
        config_file.write_text("CONCOURSE_LDAP_FILTER=(objectClass=person)\n")
        from concourse_web import ConcourseWebHelper

        result = ConcourseWebHelper._read_config(str(config_file))
        assert result == {"CONCOURSE_LDAP_FILTER": "(objectClass=person)"}

    def test_read_skips_comments_and_blank_lines(self, tmp_path):
        """Comments and blank lines are ignored."""
        config_file = tmp_path / "config.env"
        config_file.write_text("# comment\nA=1\n\n  \n# another comment\nB=2\n")
        from concourse_web import ConcourseWebHelper

        result = ConcourseWebHelper._read_config(str(config_file))
        assert result == {"A": "1", "B": "2"}


class TestWriteConfigMerge:
    """Tests for the merge behavior of _write_config."""

    def _make_web_helper(self, config=None):
        """Create a ConcourseWebHelper with a mock charm."""
        from concourse_web import ConcourseWebHelper

        charm = MagicMock()
        charm.model.config = config or {}
        helper = ConcourseWebHelper(charm)
        return helper

    @patch("concourse_web.subprocess.run")
    def test_merge_preserves_operator_keys(self, mock_run, tmp_path):
        """Operator-added keys survive when charm updates its managed keys."""
        config_file = tmp_path / "config.env"
        config_file.write_text("A=old\nB=old\nOPERATOR_KEY=keep_me\n")

        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper._write_config({"A": "new", "B": "new"})

        result = helper._read_config(str(config_file))
        assert result["A"] == "new"
        assert result["B"] == "new"
        assert result["OPERATOR_KEY"] == "keep_me"

    @patch("concourse_web.subprocess.run")
    def test_merge_on_new_file(self, mock_run, tmp_path):
        """Writing to a new (nonexistent) file works correctly."""
        config_file = tmp_path / "config.env"

        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper._write_config({"X": "1", "Y": "2"})

        result = helper._read_config(str(config_file))
        assert result == {"X": "1", "Y": "2"}

    @patch("concourse_web.subprocess.run")
    def test_output_is_sorted(self, mock_run, tmp_path):
        """Config keys are written in sorted order."""
        config_file = tmp_path / "config.env"
        config_file.write_text("ZEBRA=z\nAPPLE=a\nMANGO=m\n")

        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper._write_config({"BANANA": "b"})

        lines = config_file.read_text().strip().split("\n")
        keys = [line.split("=")[0] for line in lines]
        assert keys == sorted(keys)

    @patch("concourse_web.subprocess.run")
    def test_charm_key_overwrites_existing(self, mock_run, tmp_path):
        """Charm-managed keys overwrite their previous values."""
        config_file = tmp_path / "config.env"
        config_file.write_text("CONCOURSE_LOG_LEVEL=info\n")

        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper._write_config({"CONCOURSE_LOG_LEVEL": "debug"})

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_LOG_LEVEL"] == "debug"


class TestWorkerWriteConfigMerge:
    """Tests for the worker's merge behavior of _write_config."""

    def _make_worker_helper(self, config=None):
        """Create a ConcourseWorkerHelper with a mock charm."""
        from concourse_worker import ConcourseWorkerHelper

        charm = MagicMock()
        charm.model.config = config or {}
        helper = ConcourseWorkerHelper(charm)
        return helper

    @patch("concourse_worker.subprocess.run")
    def test_worker_merge_preserves_operator_keys(self, mock_run, tmp_path):
        """Worker's _write_config also preserves operator-added keys."""
        config_file = tmp_path / "worker-config.env"
        config_file.write_text("A=old\nCUSTOM=keep\n")

        helper = self._make_worker_helper()
        with patch.object(
            helper, "_get_worker_config_path", return_value=str(config_file)
        ):
            helper._write_config({"A": "new"})

        result = helper._read_config(str(config_file))
        assert result["A"] == "new"
        assert result["CUSTOM"] == "keep"

    @patch("concourse_worker.subprocess.run")
    def test_worker_output_is_sorted(self, mock_run, tmp_path):
        """Worker config keys are written in sorted order."""
        config_file = tmp_path / "worker-config.env"
        config_file.write_text("Z=1\nA=2\n")

        helper = self._make_worker_helper()
        with patch.object(
            helper, "_get_worker_config_path", return_value=str(config_file)
        ):
            helper._write_config({"M": "3"})

        lines = config_file.read_text().strip().split("\n")
        keys = [line.split("=")[0] for line in lines]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Worker proxy config: http-proxy / https-proxy / no-proxy
# ---------------------------------------------------------------------------


class TestWorkerProxyConfig:
    """Tests for worker proxy config options → lowercase env vars in worker-config.env."""

    def _make_worker_helper(self, config=None):
        from concourse_worker import ConcourseWorkerHelper

        charm = MagicMock()
        charm.model.config = config or {}
        charm.charm_dir = Path("/tmp/fake-charm")
        helper = ConcourseWorkerHelper(charm)
        return helper

    def _base_config(self, **overrides):
        """Minimal config that satisfies update_config() without side effects."""
        base = {
            "worker-procs": 1,
            "log-level": "info",
            "containerd-dns-proxy-enable": False,
            "containerd-dns-server": "1.1.1.1,8.8.8.8",
            "tag": "",
            "compute-runtime": "none",
            "shared-storage": "none",
            "http-proxy": "",
            "https-proxy": "",
            "no-proxy": "",
        }
        base.update(overrides)
        return base

    @patch("concourse_worker.subprocess.run")
    def test_http_proxy_written_as_lowercase(self, mock_run, tmp_path):
        """http-proxy writes http_proxy (lowercase) to worker-config.env."""
        config_file = tmp_path / "worker-config.env"
        helper = self._make_worker_helper(
            self._base_config(**{"http-proxy": "http://proxy.example.com:3128"})
        )

        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)), \
             patch.object(helper, "_setup_dataset_mount"), \
             patch("concourse_worker.Path") as mock_path:
            # Allow the worker dir creation to pass through
            mock_path.side_effect = lambda *a, **kw: Path(*a, **kw)
            helper._write_config({"http_proxy": "http://proxy.example.com:3128"})

        result = helper._read_config(str(config_file))
        assert result["http_proxy"] == "http://proxy.example.com:3128"
        assert "HTTP_PROXY" not in result, "Must not write uppercase HTTP_PROXY"

    @patch("concourse_worker.subprocess.run")
    def test_https_proxy_written_as_lowercase(self, mock_run, tmp_path):
        """https-proxy writes https_proxy (lowercase) to worker-config.env."""
        config_file = tmp_path / "worker-config.env"
        helper = self._make_worker_helper(
            self._base_config(**{"https-proxy": "http://proxy.example.com:3128"})
        )

        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)):
            helper._write_config({"https_proxy": "http://proxy.example.com:3128"})

        result = helper._read_config(str(config_file))
        assert result["https_proxy"] == "http://proxy.example.com:3128"
        assert "HTTPS_PROXY" not in result, "Must not write uppercase HTTPS_PROXY"

    @patch("concourse_worker.subprocess.run")
    def test_no_proxy_written_as_lowercase(self, mock_run, tmp_path):
        """no-proxy writes no_proxy (lowercase) to worker-config.env."""
        config_file = tmp_path / "worker-config.env"
        helper = self._make_worker_helper(
            self._base_config(**{"no-proxy": "localhost,127.0.0.1"})
        )

        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)):
            helper._write_config({"no_proxy": "localhost,127.0.0.1"})

        result = helper._read_config(str(config_file))
        assert result["no_proxy"] == "localhost,127.0.0.1"
        assert "NO_PROXY" not in result, "Must not write uppercase NO_PROXY"

    @patch("concourse_worker.subprocess.run")
    def test_empty_proxy_not_written(self, mock_run, tmp_path):
        """Empty proxy configs do not write any proxy keys to worker-config.env."""
        config_file = tmp_path / "worker-config.env"
        config_file.write_text("CONCOURSE_LOG_LEVEL=info\n")
        helper = self._make_worker_helper(self._base_config())

        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)):
            helper._write_config({"CONCOURSE_LOG_LEVEL": "info"})

        result = helper._read_config(str(config_file))
        for key in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            assert key not in result, f"Unexpected proxy key in config: {key}"

    @patch("concourse_worker.subprocess.run")
    def test_all_three_proxies_written(self, mock_run, tmp_path):
        """All three proxy values written together."""
        config_file = tmp_path / "worker-config.env"
        helper = self._make_worker_helper(self._base_config(
            **{
                "http-proxy": "http://proxy:3128",
                "https-proxy": "http://proxy:3128",
                "no-proxy": "localhost,10.0.0.0/8",
            }
        ))

        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)):
            helper._write_config({
                "http_proxy": "http://proxy:3128",
                "https_proxy": "http://proxy:3128",
                "no_proxy": "localhost,10.0.0.0/8",
            })

        result = helper._read_config(str(config_file))
        assert result["http_proxy"] == "http://proxy:3128"
        assert result["https_proxy"] == "http://proxy:3128"
        assert result["no_proxy"] == "localhost,10.0.0.0/8"

    @patch("concourse_worker.subprocess.run")
    def test_proxy_keys_are_lowercase_in_sorted_output(self, mock_run, tmp_path):
        """Lowercase proxy keys sort correctly alongside uppercase Concourse keys."""
        config_file = tmp_path / "worker-config.env"
        helper = self._make_worker_helper(self._base_config())

        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)):
            helper._write_config({
                "http_proxy": "http://proxy:3128",
                "CONCOURSE_LOG_LEVEL": "info",
                "no_proxy": "localhost",
            })

        lines = config_file.read_text().strip().split("\n")
        keys = [line.split("=")[0] for line in lines]
        # Sorted: uppercase letters come before lowercase in ASCII,
        # so CONCOURSE_* < http_proxy < no_proxy
        assert keys == sorted(keys)

    @patch("concourse_worker.subprocess.run")
    def test_update_config_reads_global_proxy_keys(self, mock_run, tmp_path):
        """update_config reads http-proxy/https-proxy/no-proxy and writes lowercase env keys."""
        config_file = tmp_path / "worker-config.env"
        helper = self._make_worker_helper(
            self._base_config(
                **{
                    "http-proxy": "http://proxy:3128",
                    "https-proxy": "http://proxy:3128",
                    "no-proxy": "localhost,10.0.0.0/8",
                }
            )
        )
        helper.worker_directory = MagicMock(work_dir=tmp_path / "worker-dir")

        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)), \
             patch.object(helper, "_setup_dataset_mount"):
            helper.update_config()

        result = helper._read_config(str(config_file))
        assert result["http_proxy"] == "http://proxy:3128"
        assert result["https_proxy"] == "http://proxy:3128"
        assert result["no_proxy"] == "localhost,10.0.0.0/8"


# ---------------------------------------------------------------------------
# Config option → env var mapping in update_config
# ---------------------------------------------------------------------------


class TestUpdateConfigMapping:
    """Tests for new config option → env var mapping in update_config."""

    def _make_web_helper(self, config):
        """Create a ConcourseWebHelper with specified config values."""
        from concourse_web import ConcourseWebHelper

        charm = MagicMock()
        charm.model.config = config
        charm.model.get_binding.side_effect = Exception("no binding")
        helper = ConcourseWebHelper(charm)
        return helper

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_proxy_mapping(self, mock_chmod, mock_run, tmp_path):
        """http-proxy/https-proxy/no-proxy map to lowercase env vars in web config."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "web-port": 8080,
                "log-level": "info",
                "initial-admin-username": "admin",
                "enable-metrics": False,
                "external-url": "http://test:8080",
                "vault-url": "",
                "encryption-key": "",
                "ldap-display-name": "",
                "ldap-host": "",
                "ldap-bind-dn": "",
                "ldap-bind-pw": "",
                "ldap-user-search-base-dn": "",
                "ldap-user-search-username": "",
                "ldap-user-search-id-attr": "",
                "ldap-user-search-email-attr": "",
                "ldap-user-search-name-attr": "",
                "ldap-user-search-filter": "",
                "ldap-group-search-base-dn": "",
                "ldap-group-search-name-attr": "",
                "ldap-group-search-user-attr": "",
                "ldap-group-search-group-attr": "",
                "ldap-group-search-filter": "",
                "main-team-ldap-group": "",
                "default-build-logs-to-retain": 0,
                "default-days-to-retain-build-logs": 0,
                "max-build-logs-to-retain": 0,
                "max-days-to-retain-build-logs": 0,
                "gc-failed-grace-period": "",
                "extra-local-users": "",
                "main-team-local-user": "",
                "http-proxy": "http://proxy:3128",
                "https-proxy": "http://proxy:3128",
                "no-proxy": "localhost,10.0.0.0/8",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["http_proxy"] == "http://proxy:3128"
        assert result["https_proxy"] == "http://proxy:3128"
        assert result["no_proxy"] == "localhost,10.0.0.0/8"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_encryption_key_mapping(self, mock_chmod, mock_run, tmp_path):
        """encryption-key maps to CONCOURSE_ENCRYPTION_KEY."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "encryption-key": "my-secret-key",
                "web-port": 8080,
                "log-level": "info",
                "initial-admin-username": "admin",
                "enable-metrics": False,
                "external-url": "http://test:8080",
                # All other new options default to empty/zero
                "vault-url": "",
                "ldap-host": "",
                "ldap-display-name": "",
                "ldap-bind-dn": "",
                "ldap-bind-pw": "",
                "ldap-user-search-base-dn": "",
                "ldap-user-search-username": "",
                "ldap-user-search-id-attr": "",
                "ldap-user-search-email-attr": "",
                "ldap-user-search-name-attr": "",
                "ldap-user-search-filter": "",
                "ldap-group-search-base-dn": "",
                "ldap-group-search-name-attr": "",
                "ldap-group-search-user-attr": "",
                "ldap-group-search-group-attr": "",
                "ldap-group-search-filter": "",
                "main-team-ldap-group": "",
                "default-build-logs-to-retain": 0,
                "default-days-to-retain-build-logs": 0,
                "max-build-logs-to-retain": 0,
                "max-days-to-retain-build-logs": 0,
                "gc-failed-grace-period": "",
                "extra-local-users": "",
                "main-team-local-user": "",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_ENCRYPTION_KEY"] == "my-secret-key"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_ldap_mapping(self, mock_chmod, mock_run, tmp_path):
        """LDAP config options map to CONCOURSE_LDAP_* env vars."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "web-port": 8080,
                "log-level": "info",
                "initial-admin-username": "admin",
                "enable-metrics": False,
                "external-url": "http://test:8080",
                "vault-url": "",
                "encryption-key": "",
                "ldap-display-name": "My LDAP",
                "ldap-host": "ldap.example.com",
                "ldap-bind-dn": "cn=admin,dc=example",
                "ldap-bind-pw": "secret",
                "ldap-user-search-base-dn": "ou=users,dc=example",
                "ldap-user-search-username": "uid",
                "ldap-user-search-id-attr": "uid",
                "ldap-user-search-email-attr": "mail",
                "ldap-user-search-name-attr": "cn",
                "ldap-user-search-filter": "(objectClass=person)",
                "ldap-group-search-base-dn": "ou=groups,dc=example",
                "ldap-group-search-name-attr": "cn",
                "ldap-group-search-user-attr": "uid",
                "ldap-group-search-group-attr": "member",
                "ldap-group-search-filter": "(objectClass=group)",
                "main-team-ldap-group": "admins,devs",
                "default-build-logs-to-retain": 0,
                "default-days-to-retain-build-logs": 0,
                "max-build-logs-to-retain": 0,
                "max-days-to-retain-build-logs": 0,
                "gc-failed-grace-period": "",
                "extra-local-users": "",
                "main-team-local-user": "",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_LDAP_DISPLAY_NAME"] == "My LDAP"
        assert result["CONCOURSE_LDAP_HOST"] == "ldap.example.com"
        assert result["CONCOURSE_LDAP_BIND_DN"] == "cn=admin,dc=example"
        assert result["CONCOURSE_LDAP_BIND_PW"] == "secret"
        assert result["CONCOURSE_LDAP_USER_SEARCH_BASE_DN"] == "ou=users,dc=example"
        assert result["CONCOURSE_LDAP_USER_SEARCH_USERNAME"] == "uid"
        assert result["CONCOURSE_LDAP_USER_SEARCH_FILTER"] == "(objectClass=person)"
        assert result["CONCOURSE_LDAP_GROUP_SEARCH_BASE_DN"] == "ou=groups,dc=example"
        assert result["CONCOURSE_LDAP_GROUP_SEARCH_FILTER"] == "(objectClass=group)"
        assert result["CONCOURSE_MAIN_TEAM_LDAP_GROUP"] == "admins,devs"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_empty_ldap_not_included(self, mock_chmod, mock_run, tmp_path):
        """Empty LDAP config values are not written to config.env."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "web-port": 8080,
                "log-level": "info",
                "initial-admin-username": "admin",
                "enable-metrics": False,
                "external-url": "http://test:8080",
                "vault-url": "",
                "encryption-key": "",
                "ldap-display-name": "",
                "ldap-host": "",
                "ldap-bind-dn": "",
                "ldap-bind-pw": "",
                "ldap-user-search-base-dn": "",
                "ldap-user-search-username": "",
                "ldap-user-search-id-attr": "",
                "ldap-user-search-email-attr": "",
                "ldap-user-search-name-attr": "",
                "ldap-user-search-filter": "",
                "ldap-group-search-base-dn": "",
                "ldap-group-search-name-attr": "",
                "ldap-group-search-user-attr": "",
                "ldap-group-search-group-attr": "",
                "ldap-group-search-filter": "",
                "main-team-ldap-group": "",
                "default-build-logs-to-retain": 0,
                "default-days-to-retain-build-logs": 0,
                "max-build-logs-to-retain": 0,
                "max-days-to-retain-build-logs": 0,
                "gc-failed-grace-period": "",
                "extra-local-users": "",
                "main-team-local-user": "",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        ldap_keys = [k for k in result if "LDAP" in k]
        assert ldap_keys == [], f"Expected no LDAP keys, got: {ldap_keys}"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_build_log_retention_mapping(self, mock_chmod, mock_run, tmp_path):
        """Build log retention config maps to correct env vars."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "web-port": 8080,
                "log-level": "info",
                "initial-admin-username": "admin",
                "enable-metrics": False,
                "external-url": "http://test:8080",
                "vault-url": "",
                "encryption-key": "",
                "ldap-display-name": "",
                "ldap-host": "",
                "ldap-bind-dn": "",
                "ldap-bind-pw": "",
                "ldap-user-search-base-dn": "",
                "ldap-user-search-username": "",
                "ldap-user-search-id-attr": "",
                "ldap-user-search-email-attr": "",
                "ldap-user-search-name-attr": "",
                "ldap-user-search-filter": "",
                "ldap-group-search-base-dn": "",
                "ldap-group-search-name-attr": "",
                "ldap-group-search-user-attr": "",
                "ldap-group-search-group-attr": "",
                "ldap-group-search-filter": "",
                "main-team-ldap-group": "",
                "default-build-logs-to-retain": 50,
                "default-days-to-retain-build-logs": 14,
                "max-build-logs-to-retain": 100,
                "max-days-to-retain-build-logs": 30,
                "gc-failed-grace-period": "1h",
                "extra-local-users": "",
                "main-team-local-user": "",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_DEFAULT_BUILD_LOGS_TO_RETAIN"] == "50"
        assert result["CONCOURSE_DEFAULT_DAYS_TO_RETAIN_BUILD_LOGS"] == "14"
        assert result["CONCOURSE_MAX_BUILD_LOGS_TO_RETAIN"] == "100"
        assert result["CONCOURSE_MAX_DAYS_TO_RETAIN_BUILD_LOGS"] == "30"
        assert result["CONCOURSE_GC_FAILED_GRACE_PERIOD"] == "1h"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_zero_retention_not_included(self, mock_chmod, mock_run, tmp_path):
        """Retention values of 0 (unlimited) are not written."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "web-port": 8080,
                "log-level": "info",
                "initial-admin-username": "admin",
                "enable-metrics": False,
                "external-url": "http://test:8080",
                "vault-url": "",
                "encryption-key": "",
                "ldap-display-name": "",
                "ldap-host": "",
                "ldap-bind-dn": "",
                "ldap-bind-pw": "",
                "ldap-user-search-base-dn": "",
                "ldap-user-search-username": "",
                "ldap-user-search-id-attr": "",
                "ldap-user-search-email-attr": "",
                "ldap-user-search-name-attr": "",
                "ldap-user-search-filter": "",
                "ldap-group-search-base-dn": "",
                "ldap-group-search-name-attr": "",
                "ldap-group-search-user-attr": "",
                "ldap-group-search-group-attr": "",
                "ldap-group-search-filter": "",
                "main-team-ldap-group": "",
                "default-build-logs-to-retain": 0,
                "default-days-to-retain-build-logs": 0,
                "max-build-logs-to-retain": 0,
                "max-days-to-retain-build-logs": 0,
                "gc-failed-grace-period": "",
                "extra-local-users": "",
                "main-team-local-user": "",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        retention_keys = [k for k in result if "RETAIN" in k or "GC_FAILED" in k]
        assert (
            retention_keys == []
        ), f"Expected no retention keys, got: {retention_keys}"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_extra_local_users_appended(self, mock_chmod, mock_run, tmp_path):
        """extra-local-users are appended to CONCOURSE_ADD_LOCAL_USER."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "web-port": 8080,
                "log-level": "info",
                "initial-admin-username": "admin",
                "enable-metrics": False,
                "external-url": "http://test:8080",
                "vault-url": "",
                "encryption-key": "",
                "ldap-display-name": "",
                "ldap-host": "",
                "ldap-bind-dn": "",
                "ldap-bind-pw": "",
                "ldap-user-search-base-dn": "",
                "ldap-user-search-username": "",
                "ldap-user-search-id-attr": "",
                "ldap-user-search-email-attr": "",
                "ldap-user-search-name-attr": "",
                "ldap-user-search-filter": "",
                "ldap-group-search-base-dn": "",
                "ldap-group-search-name-attr": "",
                "ldap-group-search-user-attr": "",
                "ldap-group-search-group-attr": "",
                "ldap-group-search-filter": "",
                "main-team-ldap-group": "",
                "default-build-logs-to-retain": 0,
                "default-days-to-retain-build-logs": 0,
                "max-build-logs-to-retain": 0,
                "max-days-to-retain-build-logs": 0,
                "gc-failed-grace-period": "",
                "extra-local-users": "oem:hash1,bot:hash2",
                "main-team-local-user": "",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        users = result["CONCOURSE_ADD_LOCAL_USER"]
        assert users == "admin:pass123,oem:hash1,bot:hash2"


# ---------------------------------------------------------------------------
# ExecStart flags in systemd service
# ---------------------------------------------------------------------------


class TestSetupSystemdService:
    """Tests for ExecStart flag additions in setup_systemd_service."""

    def _make_web_helper(self, config):
        """Create a ConcourseWebHelper with specified config values."""
        from concourse_web import ConcourseWebHelper

        charm = MagicMock()
        charm.model.config = config
        helper = ConcourseWebHelper(charm)
        return helper

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_extra_web_flags_in_execstart(self, mock_chmod, mock_run, tmp_path):
        """extra-web-flags are appended to ExecStart."""
        from concourse_web import CONCOURSE_BIN

        helper = self._make_web_helper(
            {
                "extra-web-flags": "--enable-across-step --enable-resource-causality",
            }
        )

        exec_start = f"{CONCOURSE_BIN} web"
        if helper.config.get("extra-web-flags"):
            exec_start += f" {helper.config['extra-web-flags']}"

        assert "--enable-across-step" in exec_start
        assert "--enable-resource-causality" in exec_start

    def test_old_encryption_key_via_extra_flags(self):
        """old-encryption-key can be passed via extra-web-flags."""
        from concourse_web import CONCOURSE_BIN

        helper = self._make_web_helper(
            {
                "extra-web-flags": "--old-encryption-key abc123 --enable-across-step",
            }
        )

        exec_start = f"{CONCOURSE_BIN} web"
        if helper.config.get("extra-web-flags"):
            exec_start += f" {helper.config['extra-web-flags']}"

        assert "--old-encryption-key abc123" in exec_start
        assert "--enable-across-step" in exec_start

    def test_no_flags_when_empty(self):
        """ExecStart has no extra flags when config is empty."""
        from concourse_web import CONCOURSE_BIN

        helper = self._make_web_helper(
            {
                "extra-web-flags": "",
            }
        )

        exec_start = f"{CONCOURSE_BIN} web"
        if helper.config.get("extra-web-flags"):
            exec_start += f" {helper.config['extra-web-flags']}"

        assert exec_start == f"{CONCOURSE_BIN} web"


# ---------------------------------------------------------------------------
# main-team-local-user config option
# ---------------------------------------------------------------------------


class TestMainTeamLocalUser:
    """Tests for the main-team-local-user config option."""

    # Minimal config dict shared by all tests in this class.
    BASE_CONFIG = {
        "web-port": 8080,
        "log-level": "info",
        "initial-admin-username": "admin",
        "enable-metrics": False,
        "external-url": "http://test:8080",
        "vault-url": "",
        "encryption-key": "",
        "ldap-display-name": "",
        "ldap-host": "",
        "ldap-bind-dn": "",
        "ldap-bind-pw": "",
        "ldap-user-search-base-dn": "",
        "ldap-user-search-username": "",
        "ldap-user-search-id-attr": "",
        "ldap-user-search-email-attr": "",
        "ldap-user-search-name-attr": "",
        "ldap-user-search-filter": "",
        "ldap-group-search-base-dn": "",
        "ldap-group-search-name-attr": "",
        "ldap-group-search-user-attr": "",
        "ldap-group-search-group-attr": "",
        "ldap-group-search-filter": "",
        "main-team-ldap-group": "",
        "default-build-logs-to-retain": 0,
        "default-days-to-retain-build-logs": 0,
        "max-build-logs-to-retain": 0,
        "max-days-to-retain-build-logs": 0,
        "gc-failed-grace-period": "",
        "extra-local-users": "",
        "main-team-local-user": "",
    }

    def _make_web_helper(self, overrides=None):
        from concourse_web import ConcourseWebHelper

        config = {**self.BASE_CONFIG, **(overrides or {})}
        charm = MagicMock()
        charm.model.config = config
        charm.model.get_binding.side_effect = Exception("no binding")
        return ConcourseWebHelper(charm)

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_defaults_to_initial_admin_username(self, mock_chmod, mock_run, tmp_path):
        """When main-team-local-user is not set, falls back to initial-admin-username."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper({"main-team-local-user": ""})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_MAIN_TEAM_LOCAL_USER"] == "admin"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_single_extra_user_in_main_team(self, mock_chmod, mock_run, tmp_path):
        """A single extra user set via main-team-local-user appears in CONCOURSE_MAIN_TEAM_LOCAL_USER."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper({"main-team-local-user": "oem"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_MAIN_TEAM_LOCAL_USER"] == "oem"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_multiple_users_in_main_team(self, mock_chmod, mock_run, tmp_path):
        """Multiple users set via main-team-local-user are all present in CONCOURSE_MAIN_TEAM_LOCAL_USER."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper({"main-team-local-user": "admin,oem"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_MAIN_TEAM_LOCAL_USER"] == "admin,oem"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_main_team_local_user_independent_of_extra_local_users(
        self, mock_chmod, mock_run, tmp_path
    ):
        """main-team-local-user and extra-local-users are independent.

        extra-local-users controls ADD_LOCAL_USER (credentials);
        main-team-local-user controls MAIN_TEAM_LOCAL_USER (authorization).
        Both can be set independently.
        """
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "main-team-local-user": "admin,oem",
                "extra-local-users": "oem:hash1,bot:hash2",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        # main team authorization: explicit list
        assert result["CONCOURSE_MAIN_TEAM_LOCAL_USER"] == "admin,oem"
        # credential list: admin + extra users
        assert result["CONCOURSE_ADD_LOCAL_USER"] == "admin:pass123,oem:hash1,bot:hash2"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_custom_admin_username_as_default(self, mock_chmod, mock_run, tmp_path):
        """When main-team-local-user is unset, falls back to the custom initial-admin-username."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "initial-admin-username": "ci-admin",
                "main-team-local-user": "",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_MAIN_TEAM_LOCAL_USER"] == "ci-admin"



# ---------------------------------------------------------------------------
# Generic OAuth config options
# ---------------------------------------------------------------------------


class TestGenericOAuthConfig:
    """Tests for Generic OAuth config option mapping."""

    BASE_CONFIG = {
        "web-port": 8080,
        "log-level": "info",
        "initial-admin-username": "admin",
        "enable-metrics": False,
        "external-url": "http://test:8080",
        "vault-url": "",
        "encryption-key": "",
        "ldap-display-name": "",
        "ldap-host": "",
        "ldap-bind-dn": "",
        "ldap-bind-pw": "",
        "ldap-user-search-base-dn": "",
        "ldap-user-search-username": "",
        "ldap-user-search-id-attr": "",
        "ldap-user-search-email-attr": "",
        "ldap-user-search-name-attr": "",
        "ldap-user-search-filter": "",
        "ldap-group-search-base-dn": "",
        "ldap-group-search-name-attr": "",
        "ldap-group-search-user-attr": "",
        "ldap-group-search-group-attr": "",
        "ldap-group-search-filter": "",
        "main-team-ldap-group": "",
        "default-build-logs-to-retain": 0,
        "default-days-to-retain-build-logs": 0,
        "max-build-logs-to-retain": 0,
        "max-days-to-retain-build-logs": 0,
        "gc-failed-grace-period": "",
        "extra-local-users": "",
        "main-team-local-user": "",
        "oauth-display-name": "",
        "oauth-client-id": "",
        "oauth-client-secret": "",
        "oauth-auth-url": "",
        "oauth-token-url": "",
        "oauth-userinfo-url": "",
        "oauth-scope": "",
        "oauth-groups-key": "",
        "oauth-user-id-key": "",
        "oauth-user-name-key": "",
        "oauth-ca-cert": "",
        "oauth-skip-ssl-validation": False,
        "main-team-oauth-user": "",
        "main-team-oauth-group": "",
    }

    def _make_web_helper(self, overrides=None):
        from concourse_web import ConcourseWebHelper

        config = {**self.BASE_CONFIG, **(overrides or {})}
        charm = MagicMock()
        charm.model.config = config
        charm.model.get_binding.side_effect = Exception("no binding")
        return ConcourseWebHelper(charm)

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_generic_oauth_env_mapping(self, mock_chmod, mock_run, tmp_path):
        """Generic OAuth charm config writes the corresponding Concourse env vars."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "oauth-display-name": "Launchpad",
                "oauth-client-id": "concourse",
                "oauth-client-secret": "secret",
                "oauth-auth-url": "https://lp.example.test/oauth/authorize",
                "oauth-token-url": "https://lp.example.test/oauth/token",
                "oauth-userinfo-url": "https://lp.example.test/oauth/userinfo",
                "oauth-scope": "openid profile email",
                "oauth-groups-key": "groups",
                "oauth-user-id-key": "sub",
                "oauth-user-name-key": "username",
                "oauth-ca-cert": "/etc/ssl/certs/lp-ca.pem",
                "oauth-skip-ssl-validation": True,
                "main-team-oauth-user": "alice,bob",
                "main-team-oauth-group": "ci-admins",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_OAUTH_DISPLAY_NAME"] == "Launchpad"
        assert result["CONCOURSE_OAUTH_CLIENT_ID"] == "concourse"
        assert result["CONCOURSE_OAUTH_CLIENT_SECRET"] == "secret"
        assert result["CONCOURSE_OAUTH_AUTH_URL"] == "https://lp.example.test/oauth/authorize"
        assert result["CONCOURSE_OAUTH_TOKEN_URL"] == "https://lp.example.test/oauth/token"
        assert result["CONCOURSE_OAUTH_USERINFO_URL"] == "https://lp.example.test/oauth/userinfo"
        assert result["CONCOURSE_OAUTH_SCOPE"] == "openid profile email"
        assert result["CONCOURSE_OAUTH_GROUPS_KEY"] == "groups"
        assert result["CONCOURSE_OAUTH_USER_ID_KEY"] == "sub"
        assert result["CONCOURSE_OAUTH_USER_NAME_KEY"] == "username"
        assert result["CONCOURSE_OAUTH_CA_CERT"] == "/etc/ssl/certs/lp-ca.pem"
        assert result["CONCOURSE_OAUTH_SKIP_SSL_VALIDATION"] == "true"
        assert result["CONCOURSE_MAIN_TEAM_OAUTH_USER"] == "alice,bob"
        assert result["CONCOURSE_MAIN_TEAM_OAUTH_GROUP"] == "ci-admins"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_unset_generic_oauth_options_are_not_written(self, mock_chmod, mock_run, tmp_path):
        """Empty Generic OAuth options do not enable the provider accidentally."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert "CONCOURSE_OAUTH_CLIENT_ID" not in result
        assert "CONCOURSE_OAUTH_AUTH_URL" not in result
        assert "CONCOURSE_MAIN_TEAM_OAUTH_USER" not in result
        assert "CONCOURSE_OAUTH_SKIP_SSL_VALIDATION" not in result

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_cleared_generic_oauth_options_remove_stale_env(self, mock_chmod, mock_run, tmp_path):
        """Clearing OAuth Juju config removes stale charm-managed env vars."""
        config_file = tmp_path / "config.env"
        config_file.write_text(
            "CONCOURSE_MAIN_TEAM_OAUTH_USER=alice\n"
            "CONCOURSE_MAIN_TEAM_OAUTH_GROUP=ci-admins\n"
            "CONCOURSE_OAUTH_CLIENT_ID=old-client\n"
            "CONCOURSE_OAUTH_SKIP_SSL_VALIDATION=true\n"
            "OPERATOR_KEY=keep-me\n"
        )
        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert "CONCOURSE_MAIN_TEAM_OAUTH_USER" not in result
        assert "CONCOURSE_MAIN_TEAM_OAUTH_GROUP" not in result
        assert "CONCOURSE_OAUTH_CLIENT_ID" not in result
        assert "CONCOURSE_OAUTH_SKIP_SSL_VALIDATION" not in result
        assert result["OPERATOR_KEY"] == "keep-me"


# ---------------------------------------------------------------------------
# Generic OIDC config options
# ---------------------------------------------------------------------------


class TestGenericOIDCConfig:
    """Tests for Generic OIDC config option mapping."""

    BASE_CONFIG = {
        **TestGenericOAuthConfig.BASE_CONFIG,
        "oidc-display-name": "",
        "oidc-issuer": "",
        "oidc-client-id": "",
        "oidc-client-secret": "",
        "oidc-scope": "",
        "oidc-groups-key": "",
        "oidc-user-name-key": "",
        "oidc-ca-cert": "",
        "oidc-skip-ssl-validation": False,
        "main-team-oidc-user": "",
        "main-team-oidc-group": "",
    }

    def _make_web_helper(self, overrides=None):
        from concourse_web import ConcourseWebHelper

        config = {**self.BASE_CONFIG, **(overrides or {})}
        charm = MagicMock()
        charm.model.config = config
        charm.model.get_binding.side_effect = Exception("no binding")
        return ConcourseWebHelper(charm)

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_generic_oidc_env_mapping(self, mock_chmod, mock_run, tmp_path):
        """Generic OIDC charm config writes the corresponding Concourse env vars."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "oidc-display-name": "Launchpad API",
                "oidc-issuer": "https://oidc.example.test",
                "oidc-client-id": "Concourse CI",
                "oidc-client-secret": "secret",
                "oidc-scope": "openid profile",
                "oidc-groups-key": "groups",
                "oidc-user-name-key": "username",
                "oidc-ca-cert": "/etc/ssl/certs/lp-ca.pem",
                "oidc-skip-ssl-validation": True,
                "main-team-oidc-user": "alice,bob",
                "main-team-oidc-group": "ci-admins",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_OIDC_DISPLAY_NAME"] == "Launchpad API"
        assert result["CONCOURSE_OIDC_ISSUER"] == "https://oidc.example.test"
        assert result["CONCOURSE_OIDC_CLIENT_ID"] == "Concourse CI"
        assert result["CONCOURSE_OIDC_CLIENT_SECRET"] == "secret"
        assert result["CONCOURSE_OIDC_SCOPE"] == "openid profile"
        assert result["CONCOURSE_OIDC_GROUPS_KEY"] == "groups"
        assert result["CONCOURSE_OIDC_USER_NAME_KEY"] == "username"
        assert result["CONCOURSE_OIDC_CA_CERT"] == "/etc/ssl/certs/lp-ca.pem"
        assert result["CONCOURSE_OIDC_SKIP_SSL_VALIDATION"] == "true"
        assert result["CONCOURSE_MAIN_TEAM_OIDC_USER"] == "alice,bob"
        assert result["CONCOURSE_MAIN_TEAM_OIDC_GROUP"] == "ci-admins"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_unset_generic_oidc_options_are_not_written(self, mock_chmod, mock_run, tmp_path):
        """Empty Generic OIDC options do not enable the provider accidentally."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert "CONCOURSE_OIDC_ISSUER" not in result
        assert "CONCOURSE_OIDC_CLIENT_ID" not in result
        assert "CONCOURSE_MAIN_TEAM_OIDC_USER" not in result
        assert "CONCOURSE_OIDC_SKIP_SSL_VALIDATION" not in result

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_cleared_generic_oidc_options_remove_stale_env(self, mock_chmod, mock_run, tmp_path):
        """Clearing OIDC Juju config removes stale charm-managed env vars."""
        config_file = tmp_path / "config.env"
        config_file.write_text(
            "CONCOURSE_MAIN_TEAM_OIDC_USER=alice\n"
            "CONCOURSE_MAIN_TEAM_OIDC_GROUP=ci-admins\n"
            "CONCOURSE_OIDC_ISSUER=http://old-issuer.example.test\n"
            "CONCOURSE_OIDC_SKIP_SSL_VALIDATION=true\n"
            "OPERATOR_KEY=keep-me\n"
        )
        helper = self._make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert "CONCOURSE_MAIN_TEAM_OIDC_USER" not in result
        assert "CONCOURSE_MAIN_TEAM_OIDC_GROUP" not in result
        assert "CONCOURSE_OIDC_ISSUER" not in result
        assert "CONCOURSE_OIDC_SKIP_SSL_VALIDATION" not in result
        assert result["OPERATOR_KEY"] == "keep-me"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_oauth_and_oidc_can_coexist_in_config_file(self, mock_chmod, mock_run, tmp_path):
        """OAuth and OIDC env vars can both be written when both are configured
        (e.g. during a migration window), without clobbering each other."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper(
            {
                "oauth-client-id": "old-oauth-client",
                "oauth-auth-url": "https://lp.example.test/oauth/authorize",
                "oidc-issuer": "https://oidc.example.test",
                "oidc-client-id": "Concourse CI",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_OAUTH_CLIENT_ID"] == "old-oauth-client"
        assert result["CONCOURSE_OIDC_ISSUER"] == "https://oidc.example.test"
        assert result["CONCOURSE_OIDC_CLIENT_ID"] == "Concourse CI"
