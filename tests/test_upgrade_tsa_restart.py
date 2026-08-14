#!/usr/bin/env python3
"""Regression test: worker must restart on charm upgrade so it
re-establishes its TSA session and stops advertising a stale bundled
resource-type version (e.g. `time`) after the Concourse binary was
already up to date before the charm upgrade ran.

Follows the same ops-mocking convention as tests/test_github_token.py so
this module can be collected safely alongside it in the same pytest
session (both stub out `ops`/`ops.model`/`ops.charm` before importing
charm.py).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Mock ops unconditionally for unit testing ConcourseCharm (mirrors
# tests/test_github_token.py's approach).
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
    with just enough attributes for _on_upgrade_charm to run."""
    charm = ConcourseCharm.__new__(ConcourseCharm)
    charm.unit = MagicMock()
    charm.model = MagicMock()
    charm.config = {}
    charm.worker_helper = MagicMock()
    charm.web_helper = MagicMock()
    return charm


def test_upgrade_charm_restarts_running_worker():
    """When the worker service is already running at upgrade-charm time,
    the charm must call restart_service() so the TSA session is cycled,
    even if _on_config_changed would not otherwise detect a version
    change (root cause of a real-world worker/TSA desync)."""
    charm = _make_charm()
    charm.worker_helper.is_running.return_value = True
    charm.model.get_relation.return_value = MagicMock()

    with patch.object(charm, "_should_run_web", return_value=False), patch.object(
        charm, "_should_run_worker", return_value=True
    ), patch("charm.ensure_directories"), patch(
        "charm.verify_installation", return_value=True
    ), patch(
        "charm.generate_keys"
    ), patch.object(
        charm, "_on_config_changed"
    ), patch(
        "pathlib.Path.exists", return_value=True
    ), patch(
        "pathlib.Path.read_text", return_value="fake-worker-pubkey"
    ):
        event = MagicMock()
        charm._on_upgrade_charm(event)

    charm.worker_helper.is_running.assert_called_once()
    charm.worker_helper.restart_service.assert_called_once()


def test_upgrade_charm_does_not_restart_stopped_worker():
    """If the worker service isn't running, upgrade-charm must not try to
    restart it (avoids masking start-up failures as restart failures)."""
    charm = _make_charm()
    charm.worker_helper.is_running.return_value = False
    charm.model.get_relation.return_value = MagicMock()

    with patch.object(charm, "_should_run_web", return_value=False), patch.object(
        charm, "_should_run_worker", return_value=True
    ), patch("charm.ensure_directories"), patch(
        "charm.verify_installation", return_value=True
    ), patch(
        "charm.generate_keys"
    ), patch.object(
        charm, "_on_config_changed"
    ), patch(
        "pathlib.Path.exists", return_value=True
    ), patch(
        "pathlib.Path.read_text", return_value="fake-worker-pubkey"
    ):
        event = MagicMock()
        charm._on_upgrade_charm(event)

    charm.worker_helper.restart_service.assert_not_called()
