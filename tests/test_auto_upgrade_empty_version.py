#!/usr/bin/env python3
"""Regression test: the automatic-upgrade path (juju config `version=""`,
i.e. no explicit version pinned — the charm's own update-status hook
periodically detects a newer Concourse release on GitHub and upgrades to it,
with no `juju refresh` or `juju config version=X` involved) must route
through the same coordinated-upgrade completion logic that publishes the
new version to the tsa/flight relation.

This is the actual real-world trigger reported in CEINFRA-426 CI run
31791104292 / job 94769143210: the CI step drives this via `juju config
version=X`, but the underlying charm code path
(`_orchestrate_coordinated_upgrade`) is identical to the one used by
`_on_update_status`'s automatic-upgrade-when-version-is-empty branch, so
this test exercises that second real trigger explicitly rather than relying
on it being "probably the same code".

Follows the same ops-mocking convention as
tests/test_coordinated_upgrade_tsa_publish.py so this module can be
collected safely alongside it in the same pytest session.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class DummyActiveStatus:
    def __init__(self, message=""):
        self.message = message


class DummyWaitingStatus:
    def __init__(self, message=""):
        self.message = message


class DummyBlockedStatus:
    def __init__(self, message=""):
        self.message = message


class DummyMaintenanceStatus:
    def __init__(self, message=""):
        self.message = message


ops_model = MagicMock()
ops_model.ActiveStatus = DummyActiveStatus
ops_model.WaitingStatus = DummyWaitingStatus
ops_model.BlockedStatus = DummyBlockedStatus
ops_model.MaintenanceStatus = DummyMaintenanceStatus
sys.modules["ops.model"] = ops_model

ops_charm = MagicMock()
ops_charm.CharmBase = type(
    "CharmBase", (object,), {"__init__": lambda self, *a, **kw: None}
)
sys.modules["ops.charm"] = ops_charm

sys.modules["ops"] = MagicMock()
sys.modules["ops.main"] = MagicMock()

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from charm import ConcourseCharm  # noqa: E402


def _make_charm():
    """Build a ConcourseCharm instance without running ops.CharmBase.__init__,
    with just enough attributes for _on_update_status's auto-upgrade branch
    to run."""
    charm = ConcourseCharm.__new__(ConcourseCharm)
    charm.unit = MagicMock()
    charm.unit.is_leader.return_value = True
    charm.unit.status = DummyWaitingStatus("Waiting for leader to install Concourse 8.2.5")
    charm.model = MagicMock()
    # config["version"] == "" (the default) means "no version pinned,
    # auto-upgrade to latest" — the real-world scenario reported by the
    # user, as opposed to an explicit `juju config version=X`.
    charm.config = {"version": "", "shared-storage": "lxc"}
    charm.web_helper = MagicMock()
    charm.worker_helper = MagicMock()
    charm.app = MagicMock()
    return charm


def test_auto_upgrade_with_empty_version_config_uses_coordinated_upgrade():
    """When version="" (unset) and shared-storage="lxc", update-status
    detecting a newer release must route through
    _orchestrate_coordinated_upgrade (not silently skip worker
    notification), so the tsa/flight-relation publish fix also covers the
    "no juju refresh, no juju config version=X" automatic-upgrade trigger."""
    charm = _make_charm()

    fake_peer_relation = MagicMock()
    charm.model.get_relation.return_value = fake_peer_relation

    with patch.object(
        charm, "_should_run_web", return_value=True
    ), patch(
        "charm.get_concourse_version", return_value="8.3.0"
    ), patch.object(
        charm, "_get_installed_concourse_version", return_value="8.2.5"
    ), patch.object(
        charm, "_orchestrate_coordinated_upgrade"
    ) as mock_orchestrate, patch.object(
        charm, "_publish_version_to_peer_relation"
    ), patch.object(
        charm, "_update_status"
    ):
        event = MagicMock()
        charm._on_update_status(event)

    mock_orchestrate.assert_called_once()
    called_args, _ = mock_orchestrate.call_args
    # Second positional arg is the desired version to upgrade to.
    assert called_args[1] == "8.3.0"
