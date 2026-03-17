#!/usr/bin/env python3
"""Unit tests for config.env merge behavior and new config option mapping."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)):
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
        with patch.object(helper, "_get_worker_config_path", return_value=str(config_file)):
            helper._write_config({"M": "3"})

        lines = config_file.read_text().strip().split("\n")
        keys = [line.split("=")[0] for line in lines]
        assert keys == sorted(keys)


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
    def test_encryption_key_mapping(self, mock_chmod, mock_run, tmp_path):
        """encryption-key maps to CONCOURSE_ENCRYPTION_KEY."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper({
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
        })
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_ENCRYPTION_KEY"] == "my-secret-key"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_ldap_mapping(self, mock_chmod, mock_run, tmp_path):
        """LDAP config options map to CONCOURSE_LDAP_* env vars."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper({
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
            "main-team-ldap-group": "ldap:admins,ldap:devs",
            "default-build-logs-to-retain": 0,
            "default-days-to-retain-build-logs": 0,
            "max-build-logs-to-retain": 0,
            "max-days-to-retain-build-logs": 0,
            "gc-failed-grace-period": "",
            "extra-local-users": "",
        })
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
        assert result["CONCOURSE_MAIN_TEAM_LDAP_GROUP"] == "ldap:admins,ldap:devs"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_empty_ldap_not_included(self, mock_chmod, mock_run, tmp_path):
        """Empty LDAP config values are not written to config.env."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper({
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
        })
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
        helper = self._make_web_helper({
            "web-port": 8080,
            "log-level": "info",
            "initial-admin-username": "admin",
            "enable-metrics": False,
            "external-url": "http://test:8080",
            "vault-url": "",
            "encryption-key": "",
            "ldap-display-name": "", "ldap-host": "", "ldap-bind-dn": "",
            "ldap-bind-pw": "", "ldap-user-search-base-dn": "",
            "ldap-user-search-username": "", "ldap-user-search-id-attr": "",
            "ldap-user-search-email-attr": "", "ldap-user-search-name-attr": "",
            "ldap-user-search-filter": "", "ldap-group-search-base-dn": "",
            "ldap-group-search-name-attr": "", "ldap-group-search-user-attr": "",
            "ldap-group-search-group-attr": "", "ldap-group-search-filter": "",
            "main-team-ldap-group": "",
            "default-build-logs-to-retain": 50,
            "default-days-to-retain-build-logs": 14,
            "max-build-logs-to-retain": 100,
            "max-days-to-retain-build-logs": 30,
            "gc-failed-grace-period": "1h",
            "extra-local-users": "",
        })
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
        helper = self._make_web_helper({
            "web-port": 8080,
            "log-level": "info",
            "initial-admin-username": "admin",
            "enable-metrics": False,
            "external-url": "http://test:8080",
            "vault-url": "",
            "encryption-key": "",
            "ldap-display-name": "", "ldap-host": "", "ldap-bind-dn": "",
            "ldap-bind-pw": "", "ldap-user-search-base-dn": "",
            "ldap-user-search-username": "", "ldap-user-search-id-attr": "",
            "ldap-user-search-email-attr": "", "ldap-user-search-name-attr": "",
            "ldap-user-search-filter": "", "ldap-group-search-base-dn": "",
            "ldap-group-search-name-attr": "", "ldap-group-search-user-attr": "",
            "ldap-group-search-group-attr": "", "ldap-group-search-filter": "",
            "main-team-ldap-group": "",
            "default-build-logs-to-retain": 0,
            "default-days-to-retain-build-logs": 0,
            "max-build-logs-to-retain": 0,
            "max-days-to-retain-build-logs": 0,
            "gc-failed-grace-period": "",
            "extra-local-users": "",
        })
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        retention_keys = [k for k in result if "RETAIN" in k or "GC_FAILED" in k]
        assert retention_keys == [], f"Expected no retention keys, got: {retention_keys}"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_extra_local_users_appended(self, mock_chmod, mock_run, tmp_path):
        """extra-local-users are appended to CONCOURSE_ADD_LOCAL_USER."""
        config_file = tmp_path / "config.env"
        helper = self._make_web_helper({
            "web-port": 8080,
            "log-level": "info",
            "initial-admin-username": "admin",
            "enable-metrics": False,
            "external-url": "http://test:8080",
            "vault-url": "",
            "encryption-key": "",
            "ldap-display-name": "", "ldap-host": "", "ldap-bind-dn": "",
            "ldap-bind-pw": "", "ldap-user-search-base-dn": "",
            "ldap-user-search-username": "", "ldap-user-search-id-attr": "",
            "ldap-user-search-email-attr": "", "ldap-user-search-name-attr": "",
            "ldap-user-search-filter": "", "ldap-group-search-base-dn": "",
            "ldap-group-search-name-attr": "", "ldap-group-search-user-attr": "",
            "ldap-group-search-group-attr": "", "ldap-group-search-filter": "",
            "main-team-ldap-group": "",
            "default-build-logs-to-retain": 0,
            "default-days-to-retain-build-logs": 0,
            "max-build-logs-to-retain": 0,
            "max-days-to-retain-build-logs": 0,
            "gc-failed-grace-period": "",
            "extra-local-users": "oem:hash1,bot:hash2",
        })
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

        helper = self._make_web_helper({
            "extra-web-flags": "--enable-across-step --enable-resource-causality",
        })

        exec_start = f"{CONCOURSE_BIN} web"
        if helper.config.get("extra-web-flags"):
            exec_start += f" {helper.config['extra-web-flags']}"

        assert "--enable-across-step" in exec_start
        assert "--enable-resource-causality" in exec_start

    def test_old_encryption_key_via_extra_flags(self):
        """old-encryption-key can be passed via extra-web-flags."""
        from concourse_web import CONCOURSE_BIN

        helper = self._make_web_helper({
            "extra-web-flags": "--old-encryption-key abc123 --enable-across-step",
        })

        exec_start = f"{CONCOURSE_BIN} web"
        if helper.config.get("extra-web-flags"):
            exec_start += f" {helper.config['extra-web-flags']}"

        assert "--old-encryption-key abc123" in exec_start
        assert "--enable-across-step" in exec_start

    def test_no_flags_when_empty(self):
        """ExecStart has no extra flags when config is empty."""
        from concourse_web import CONCOURSE_BIN

        helper = self._make_web_helper({
            "extra-web-flags": "",
        })

        exec_start = f"{CONCOURSE_BIN} web"
        if helper.config.get("extra-web-flags"):
            exec_start += f" {helper.config['extra-web-flags']}"

        assert exec_start == f"{CONCOURSE_BIN} web"
