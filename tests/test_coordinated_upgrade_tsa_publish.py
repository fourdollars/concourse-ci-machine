#!/usr/bin/env python3
"""Regression test: a coordinated (shared-storage) upgrade must publish the
new Concourse version to the tsa/flight relation so worker units (which may
live in a separate Juju app in web+worker mode, and therefore cannot see the
web app's own `peers` relation data) actually observe the version change and
restart their worker service via _on_tsa_relation_changed.

Without this, `_orchestrate_coordinated_upgrade` only wrote the "complete"
signal to its own app's peers relation. In single-app "auto" mode that's
enough (web and worker share the same peers relation), but in web+worker
mode (separate apps) the worker never saw the signal, so the worker service
was never restarted, leaving it stuck advertising a stale TSA registration/
resource-type version even though its binary had already been upgraded via
shared storage.

Follows the same ops-mocking convention as tests/test_upgrade_tsa_restart.py
so this module can be collected safely alongside it in the same pytest
session (both stub out `ops`/`ops.model`/`ops.charm` before importing
charm.py).
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
    with just enough attributes for _orchestrate_coordinated_upgrade to run."""
    charm = ConcourseCharm.__new__(ConcourseCharm)
    charm.unit = MagicMock()
    charm.model = MagicMock()
    charm.config = {}
    charm.web_helper = MagicMock()
    charm.worker_helper = MagicMock()
    charm.app = MagicMock()
    return charm


def test_coordinated_upgrade_publishes_version_to_tsa_relations():
    """After a coordinated (shared-storage) upgrade completes, the charm
    must publish the new version to the tsa/flight relation (cross-app
    visible) — not just to its own app's peers relation — so worker units
    in a separate app actually see the version change and restart via
    _on_tsa_relation_changed."""
    charm = _make_charm()

    fake_storage_coordinator = MagicMock()
    charm.web_helper.storage_coordinator = fake_storage_coordinator

    fake_peer_relation = MagicMock()
    fake_peer_relation.units = []
    charm.model.get_relation.return_value = fake_peer_relation

    with patch(
        "storage_coordinator.UpgradeCoordinator"
    ) as MockCoordinatorCls, patch("storage_coordinator.RelationDataAccessor"), patch(
        "storage_coordinator.ServiceManager"
    ), patch(
        "concourse_installer.download_and_install_concourse_with_storage"
    ), patch.object(
        charm, "_publish_version_to_tsa_relations"
    ) as mock_publish:
        mock_coordinator = MockCoordinatorCls.return_value
        mock_coordinator.initiate_upgrade.return_value = None
        mock_coordinator.mark_download_phase.return_value = None
        mock_coordinator.complete_upgrade.return_value = None
        mock_coordinator.reset_upgrade_state.return_value = None

        with patch("time.sleep"):
            charm._orchestrate_coordinated_upgrade(None, "8.3.0")

    mock_coordinator.complete_upgrade.assert_called_once()
    mock_publish.assert_called_once()
