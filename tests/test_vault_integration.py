#!/usr/bin/env python3
"""Unit tests for HashiCorp Vault integration via vault-kv relation and manual vault-url config."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

# ---------------------------------------------------------------------------
# Shared base config used across test classes
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "web-port": 8080,
    "log-level": "info",
    "initial-admin-username": "admin",
    "enable-metrics": False,
    "external-url": "http://test:8080",
    "vault-url": "",
    "vault-path-prefix": "",
    "vault-auth-backend": "",
    "vault-auth-backend-max-ttl": "",
    "vault-auth-param": "",
    "vault-ca-cert": "",
    "vault-client-cert": "",
    "vault-client-key": "",
    "vault-client-token": "",
    "vault-lookup-templates": "",
    "vault-namespace": "",
    "vault-shared-path": "",
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
    "secret-cache-enabled": True,
    "secret-cache-duration": "1m",
    "secret-cache-duration-notfound": "10s",
}


def _make_web_helper(overrides=None):
    """Return a ConcourseWebHelper backed by a MagicMock charm."""
    from concourse_web import ConcourseWebHelper

    config = {**_BASE_CONFIG, **(overrides or {})}
    charm = MagicMock()
    charm.model.config = config
    charm.model.get_binding.side_effect = Exception("no binding")
    return ConcourseWebHelper(charm)


# ---------------------------------------------------------------------------
# vault-kv relation path: vault_kv_config dict passed to update_config()
# ---------------------------------------------------------------------------


class TestVaultKvRelationConfig:
    """Tests for the vault-kv relation code path (vault_kv_config dict)."""

    # A typical vault_kv_config dict as produced by ConcourseCharm._get_vault_kv_config()
    VAULT_KV_CONFIG = {
        "CONCOURSE_VAULT_URL": "https://vault.example.com:8200",
        "CONCOURSE_VAULT_AUTH_BACKEND": "approle",
        "CONCOURSE_VAULT_AUTH_PARAM": "role-id=abc123,secret-id=def456",
        "CONCOURSE_VAULT_CA_CERT": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
        "CONCOURSE_VAULT_PATH_PREFIX": "/charm-web-concourse",
    }

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_kv_config_written_to_env(self, mock_chmod, mock_run, tmp_path):
        """All five VAULT env vars from vault_kv_config appear in config.env."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(
                admin_password="pass123", vault_kv_config=self.VAULT_KV_CONFIG
            )

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_URL"] == "https://vault.example.com:8200"
        assert result["CONCOURSE_VAULT_AUTH_BACKEND"] == "approle"
        assert result["CONCOURSE_VAULT_AUTH_PARAM"] == "role-id=abc123,secret-id=def456"
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/charm-web-concourse"
        assert "-----BEGIN CERTIFICATE-----" in result["CONCOURSE_VAULT_CA_CERT"]

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_kv_config_takes_precedence_over_vault_url(
        self, mock_chmod, mock_run, tmp_path
    ):
        """vault_kv_config overrides manual vault-url when both are present."""
        config_file = tmp_path / "config.env"
        # Charm config also has a manual vault-url set
        helper = _make_web_helper({"vault-url": "https://manual-vault:8200"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(
                admin_password="pass123", vault_kv_config=self.VAULT_KV_CONFIG
            )

        result = helper._read_config(str(config_file))
        # vault_kv_config URL wins
        assert result["CONCOURSE_VAULT_URL"] == "https://vault.example.com:8200"
        assert result["CONCOURSE_VAULT_AUTH_BACKEND"] == "approle"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_kv_config_none_falls_back_to_vault_url(
        self, mock_chmod, mock_run, tmp_path
    ):
        """When vault_kv_config is None, manual vault-url config is used instead."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"vault-url": "https://manual-vault:8200"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=None)

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_URL"] == "https://manual-vault:8200"
        # AppRole auth not present; manual path has no auth-backend configured
        assert "CONCOURSE_VAULT_AUTH_BACKEND" not in result

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_no_vault_vars_when_neither_configured(
        self, mock_chmod, mock_run, tmp_path
    ):
        """No VAULT env vars appear in config.env when neither vault-url nor vault_kv_config is set."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper()  # vault-url = ""
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=None)

        result = helper._read_config(str(config_file))
        vault_keys = [k for k in result if k.startswith("CONCOURSE_VAULT_")]
        assert vault_keys == [], f"Unexpected VAULT keys: {vault_keys}"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_kv_config_does_not_leak_into_non_vault_keys(
        self, mock_chmod, mock_run, tmp_path
    ):
        """vault_kv_config does not accidentally overwrite non-VAULT keys like CONCOURSE_LOG_LEVEL."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"log-level": "debug"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(
                admin_password="pass123", vault_kv_config=self.VAULT_KV_CONFIG
            )

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_LOG_LEVEL"] == "debug"
        assert result["CONCOURSE_VAULT_AUTH_BACKEND"] == "approle"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_kv_config_path_prefix_in_env(self, mock_chmod, mock_run, tmp_path):
        """CONCOURSE_VAULT_PATH_PREFIX from vault_kv_config uses the mount as prefix."""
        config_file = tmp_path / "config.env"
        kv_config = {
            **self.VAULT_KV_CONFIG,
            "CONCOURSE_VAULT_PATH_PREFIX": "/charm-web-concourse",
        }
        helper = _make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=kv_config)

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/charm-web-concourse"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_kv_config_stale_vars_overwritten_on_update(
        self, mock_chmod, mock_run, tmp_path
    ):
        """Re-calling update_config with new vault_kv_config updates stale VAULT values."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper()

        first_kv = {
            "CONCOURSE_VAULT_URL": "https://vault-old:8200",
            "CONCOURSE_VAULT_AUTH_BACKEND": "approle",
            "CONCOURSE_VAULT_AUTH_PARAM": "role-id=old,secret-id=old",
            "CONCOURSE_VAULT_CA_CERT": "OLD-CERT",
            "CONCOURSE_VAULT_PATH_PREFIX": "/old-mount",
        }
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=first_kv)

        second_kv = {
            "CONCOURSE_VAULT_URL": "https://vault-new:8200",
            "CONCOURSE_VAULT_AUTH_BACKEND": "approle",
            "CONCOURSE_VAULT_AUTH_PARAM": "role-id=new,secret-id=new",
            "CONCOURSE_VAULT_CA_CERT": "NEW-CERT",
            "CONCOURSE_VAULT_PATH_PREFIX": "/new-mount",
        }
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=second_kv)

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_URL"] == "https://vault-new:8200"
        assert result["CONCOURSE_VAULT_AUTH_PARAM"] == "role-id=new,secret-id=new"
        assert result["CONCOURSE_VAULT_CA_CERT"] == "NEW-CERT"
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/new-mount"


# ---------------------------------------------------------------------------
# Manual vault-url config path (no vault-kv relation)
# ---------------------------------------------------------------------------


class TestManualVaultUrlConfig:
    """Tests for the manual vault-url config option (no vault-kv relation)."""

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_url_written_to_env(self, mock_chmod, mock_run, tmp_path):
        """vault-url maps to CONCOURSE_VAULT_URL."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"vault-url": "https://vault.example.com:8200"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_URL"] == "https://vault.example.com:8200"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_auth_backend_written(self, mock_chmod, mock_run, tmp_path):
        """vault-auth-backend maps to CONCOURSE_VAULT_AUTH_BACKEND."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper(
            {
                "vault-url": "https://vault.example.com:8200",
                "vault-auth-backend": "approle",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_AUTH_BACKEND"] == "approle"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_path_prefix_written(self, mock_chmod, mock_run, tmp_path):
        """vault-path-prefix maps to CONCOURSE_VAULT_PATH_PREFIX."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper(
            {
                "vault-url": "https://vault.example.com:8200",
                "vault-path-prefix": "/secrets",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/secrets"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_ca_cert_written(self, mock_chmod, mock_run, tmp_path):
        """vault-ca-cert maps to CONCOURSE_VAULT_CA_CERT."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper(
            {
                "vault-url": "https://vault.example.com:8200",
                "vault-ca-cert": "-----BEGIN CERTIFICATE-----\nMIIB...",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert "CONCOURSE_VAULT_CA_CERT" in result

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_optional_fields_absent_when_empty(
        self, mock_chmod, mock_run, tmp_path
    ):
        """Optional vault-* config keys are not written to config.env when empty."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"vault-url": "https://vault.example.com:8200"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert "CONCOURSE_VAULT_AUTH_BACKEND" not in result
        assert "CONCOURSE_VAULT_PATH_PREFIX" not in result
        assert "CONCOURSE_VAULT_CA_CERT" not in result
        assert "CONCOURSE_VAULT_CLIENT_CERT" not in result
        assert "CONCOURSE_VAULT_CLIENT_KEY" not in result
        assert "CONCOURSE_VAULT_CLIENT_TOKEN" not in result
        assert "CONCOURSE_VAULT_NAMESPACE" not in result
        assert "CONCOURSE_VAULT_SHARED_PATH" not in result

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_all_manual_fields_written(self, mock_chmod, mock_run, tmp_path):
        """All manual vault config options map to their CONCOURSE_VAULT_* counterparts."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper(
            {
                "vault-url": "https://vault.example.com:8200",
                "vault-auth-backend": "approle",
                "vault-auth-backend-max-ttl": "768h",
                "vault-auth-param": "role-id=abc,secret-id=def",
                "vault-ca-cert": "CERT_DATA",
                "vault-client-cert": "CLIENT_CERT",
                "vault-client-key": "CLIENT_KEY",
                "vault-client-token": "s.token",
                "vault-lookup-templates": "/{{.Team}}/{{.Pipeline}}/{{.Secret}}",
                "vault-namespace": "admin/concourse",
                "vault-path-prefix": "/concourse",
                "vault-shared-path": "/shared",
            }
        )
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_URL"] == "https://vault.example.com:8200"
        assert result["CONCOURSE_VAULT_AUTH_BACKEND"] == "approle"
        assert result["CONCOURSE_VAULT_AUTH_BACKEND_MAX_TTL"] == "768h"
        assert result["CONCOURSE_VAULT_AUTH_PARAM"] == "role-id=abc,secret-id=def"
        assert result["CONCOURSE_VAULT_CA_CERT"] == "CERT_DATA"
        assert result["CONCOURSE_VAULT_CLIENT_CERT"] == "CLIENT_CERT"
        assert result["CONCOURSE_VAULT_CLIENT_KEY"] == "CLIENT_KEY"
        assert result["CONCOURSE_VAULT_CLIENT_TOKEN"] == "s.token"
        assert (
            result["CONCOURSE_VAULT_LOOKUP_TEMPLATES"]
            == "/{{.Team}}/{{.Pipeline}}/{{.Secret}}"
        )
        assert result["CONCOURSE_VAULT_NAMESPACE"] == "admin/concourse"
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/concourse"
        assert result["CONCOURSE_VAULT_SHARED_PATH"] == "/shared"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_url_empty_means_no_vault_vars(
        self, mock_chmod, mock_run, tmp_path
    ):
        """An empty vault-url produces no CONCOURSE_VAULT_* vars in config.env."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"vault-url": ""})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        vault_keys = [k for k in result if k.startswith("CONCOURSE_VAULT_")]
        assert vault_keys == [], f"Unexpected VAULT keys: {vault_keys}"


# ---------------------------------------------------------------------------
# vault-path-prefix override when vault-kv relation is active
# ---------------------------------------------------------------------------


class TestVaultPathPrefixOverride:
    """Tests for vault-path-prefix juju config overriding the vault-kv mount path."""

    BASE_KV = {
        "CONCOURSE_VAULT_URL": "https://vault.example.com:8200",
        "CONCOURSE_VAULT_AUTH_BACKEND": "approle",
        "CONCOURSE_VAULT_AUTH_PARAM": "role-id=abc,secret-id=def",
        "CONCOURSE_VAULT_CA_CERT": "CERT",
        "CONCOURSE_VAULT_PATH_PREFIX": "/charm-web-concourse",  # set by _get_vault_kv_config
    }

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_kv_path_prefix_used_from_kv_config(
        self, mock_chmod, mock_run, tmp_path
    ):
        """When vault-path-prefix charm config is empty, the path from vault_kv_config is used."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"vault-path-prefix": ""})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=self.BASE_KV)

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/charm-web-concourse"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_charm_config_overrides_kv_mount_in_passed_dict(
        self, mock_chmod, mock_run, tmp_path
    ):
        """vault-path-prefix override is applied by caller before passing vault_kv_config;
        the value in vault_kv_config reflects whatever _get_vault_kv_config resolved."""
        config_file = tmp_path / "config.env"
        # Simulate _get_vault_kv_config already having applied the override
        kv_with_override = {
            **self.BASE_KV,
            "CONCOURSE_VAULT_PATH_PREFIX": "/secrets",
        }
        helper = _make_web_helper({"vault-path-prefix": "/secrets"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(
                admin_password="pass123", vault_kv_config=kv_with_override
            )

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/secrets"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_leading_slash_normalised_in_path_prefix(
        self, mock_chmod, mock_run, tmp_path
    ):
        """Path prefix with leading slash is written as-is; without slash it is preserved as given."""
        config_file = tmp_path / "config.env"
        kv_config = {**self.BASE_KV, "CONCOURSE_VAULT_PATH_PREFIX": "/my-prefix"}
        helper = _make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=kv_config)

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_VAULT_PATH_PREFIX"] == "/my-prefix"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_manual_vault_path_prefix_without_vault_url(
        self, mock_chmod, mock_run, tmp_path
    ):
        """vault-path-prefix config without vault-url has no effect (no VAULT vars written)."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"vault-url": "", "vault-path-prefix": "/secrets"})
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        vault_keys = [k for k in result if k.startswith("CONCOURSE_VAULT_")]
        assert vault_keys == [], f"Unexpected VAULT keys: {vault_keys}"


# ---------------------------------------------------------------------------
# Vault removal / gone-away: stale VAULT vars are cleared from config.env
# ---------------------------------------------------------------------------


class TestVaultKvGoneAway:
    """Tests verifying that VAULT env vars are cleaned up when vault-kv relation is removed."""

    VAULT_KV_CONFIG = {
        "CONCOURSE_VAULT_URL": "https://vault.example.com:8200",
        "CONCOURSE_VAULT_AUTH_BACKEND": "approle",
        "CONCOURSE_VAULT_AUTH_PARAM": "role-id=abc,secret-id=def",
        "CONCOURSE_VAULT_CA_CERT": "CERT",
        "CONCOURSE_VAULT_PATH_PREFIX": "/charm-web-concourse",
    }

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_vault_vars_absent_after_relation_removed(
        self, mock_chmod, mock_run, tmp_path
    ):
        """Calling update_config(vault_kv_config=None) after relation gone clears VAULT vars."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper()

        # First, relation is active → VAULT vars written
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(
                admin_password="pass123", vault_kv_config=self.VAULT_KV_CONFIG
            )

        result = helper._read_config(str(config_file))
        assert "CONCOURSE_VAULT_URL" in result

        # Relation removed → update_config called again with vault_kv_config=None, no vault-url
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=None)

        result = helper._read_config(str(config_file))
        vault_keys = [k for k in result if k.startswith("CONCOURSE_VAULT_")]
        assert vault_keys == [], f"Stale VAULT keys remain: {vault_keys}"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_non_vault_keys_survive_after_relation_removed(
        self, mock_chmod, mock_run, tmp_path
    ):
        """Non-VAULT keys (e.g. CONCOURSE_LOG_LEVEL) are not affected when VAULT vars are removed."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({"log-level": "debug"})

        # Add vault via relation
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(
                admin_password="pass123", vault_kv_config=self.VAULT_KV_CONFIG
            )

        # Remove relation
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=None)

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_LOG_LEVEL"] == "debug"
        vault_keys = [k for k in result if k.startswith("CONCOURSE_VAULT_")]
        assert vault_keys == [], f"Stale VAULT keys remain: {vault_keys}"


# ---------------------------------------------------------------------------
# Credential caching (CONCOURSE_SECRET_CACHE_*)
# ---------------------------------------------------------------------------


class TestSecretCache:
    """Tests for secret-cache-enabled / duration / duration-notfound config options."""

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_cache_enabled_by_default(self, mock_chmod, mock_run, tmp_path):
        """With default config (secret-cache-enabled=True), cache env vars are written."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper()
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_SECRET_CACHE_ENABLED"] == "true"
        assert result["CONCOURSE_SECRET_CACHE_DURATION"] == "1m"
        assert result["CONCOURSE_SECRET_CACHE_DURATION_NOTFOUND"] == "10s"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_cache_disabled(self, mock_chmod, mock_run, tmp_path):
        """When secret-cache-enabled=False, ENABLED is false and duration vars are absent."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({
            "secret-cache-enabled": False,
            "secret-cache-duration": "1m",
            "secret-cache-duration-notfound": "10s",
        })
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_SECRET_CACHE_ENABLED"] == "false"
        assert "CONCOURSE_SECRET_CACHE_DURATION" not in result
        assert "CONCOURSE_SECRET_CACHE_DURATION_NOTFOUND" not in result

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_custom_cache_duration(self, mock_chmod, mock_run, tmp_path):
        """Custom duration values are written when cache is enabled."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({
            "secret-cache-enabled": True,
            "secret-cache-duration": "5m",
            "secret-cache-duration-notfound": "30s",
        })
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_SECRET_CACHE_ENABLED"] == "true"
        assert result["CONCOURSE_SECRET_CACHE_DURATION"] == "5m"
        assert result["CONCOURSE_SECRET_CACHE_DURATION_NOTFOUND"] == "30s"

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_cache_keys_cleaned_up_when_disabled(self, mock_chmod, mock_run, tmp_path):
        """Disabling cache removes DURATION and DURATION_NOTFOUND from config.env."""
        config_file = tmp_path / "config.env"

        # First enable
        helper = _make_web_helper({
            "secret-cache-enabled": True,
            "secret-cache-duration": "5m",
            "secret-cache-duration-notfound": "30s",
        })
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123")

        result = helper._read_config(str(config_file))
        assert result["CONCOURSE_SECRET_CACHE_DURATION"] == "5m"

        # Now disable
        helper2 = _make_web_helper({
            "secret-cache-enabled": False,
            "secret-cache-duration": "5m",
            "secret-cache-duration-notfound": "30s",
        })
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper2.update_config(admin_password="pass123")

        result2 = helper2._read_config(str(config_file))
        assert result2["CONCOURSE_SECRET_CACHE_ENABLED"] == "false"
        assert "CONCOURSE_SECRET_CACHE_DURATION" not in result2
        assert "CONCOURSE_SECRET_CACHE_DURATION_NOTFOUND" not in result2

    @patch("concourse_web.subprocess.run")
    @patch("concourse_web.os.chmod")
    def test_cache_works_alongside_vault_kv_config(self, mock_chmod, mock_run, tmp_path):
        """Cache vars are written even when vault-kv relation is active."""
        config_file = tmp_path / "config.env"
        helper = _make_web_helper({
            "secret-cache-enabled": True,
            "secret-cache-duration": "2m",
            "secret-cache-duration-notfound": "15s",
        })
        vault_kv_config = {
            "CONCOURSE_VAULT_URL": "https://vault.example.com:8200",
            "CONCOURSE_VAULT_AUTH_BACKEND": "approle",
            "CONCOURSE_VAULT_AUTH_PARAM": "role_id:abc,secret_id:def",
            "CONCOURSE_VAULT_CA_CERT": "/etc/concourse/vault-ca.pem",
            "CONCOURSE_VAULT_PATH_PREFIX": "/charm-web-concourse",
        }
        with patch("concourse_web.CONCOURSE_CONFIG_FILE", str(config_file)):
            helper.update_config(admin_password="pass123", vault_kv_config=vault_kv_config)

        result = helper._read_config(str(config_file))
        # Vault vars present
        assert result["CONCOURSE_VAULT_URL"] == "https://vault.example.com:8200"
        # Cache vars also present
        assert result["CONCOURSE_SECRET_CACHE_ENABLED"] == "true"
        assert result["CONCOURSE_SECRET_CACHE_DURATION"] == "2m"
        assert result["CONCOURSE_SECRET_CACHE_DURATION_NOTFOUND"] == "15s"
