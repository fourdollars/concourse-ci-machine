#!/usr/bin/env python3
"""Regression tests: `_update_status` must invoke the DB-health circuit
breaker for web units and, if it reports the circuit as open (DB errors
persisted through the automatic-restart budget), set BlockedStatus so a
human is alerted instead of the charm silently reporting ActiveStatus
while Concourse is actually broken for end users.

Follows the same ops-mocking convention as
tests/test_upgrade_tsa_restart.py / tests/test_github_token.py so this
module can be collected safely alongside them in the same pytest
session.
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

# `charm.py` captures whichever ActiveStatus/BlockedStatus classes were
# present in `sys.modules["ops.model"]` at import time. Since multiple test
# modules in this suite install their own Dummy* stand-ins there, the
# classes actually bound inside charm.py may differ (by identity) from the
# Dummy* classes defined above once other test modules have already run in
# the same pytest session. Assert against the classes charm.py itself
# resolved, not our locally-defined ones, so these tests are order-independent.
from charm import ActiveStatus as CharmActiveStatus  # noqa: E402
from charm import BlockedStatus as CharmBlockedStatus  # noqa: E402


def _make_charm():
    charm = ConcourseCharm.__new__(ConcourseCharm)
    charm.unit = MagicMock()
    charm.model = MagicMock()
    charm.config = {"shared-storage": "none", "web-port": 8080}
    charm.worker_helper = MagicMock()
    charm.web_helper = MagicMock()
    return charm


def _patch_common(charm, **overrides):
    """Common patches so _update_status reaches the web-mode DB checks."""
    patches = dict(
        verify_installation=patch("charm.verify_installation", return_value=True),
        should_run_web=patch.object(charm, "_should_run_web", return_value=True),
        should_run_worker=patch.object(charm, "_should_run_worker", return_value=False),
        get_url=patch.object(
            charm, "_get_postgresql_url", return_value="postgres://u:p@h:5432/concourse"
        ),
        get_mode=patch.object(charm, "_get_deployment_mode", return_value="web"),
        get_version=patch.object(
            charm, "_get_installed_concourse_version", return_value="8.3.0"
        ),
        manage_port=patch.object(charm, "_manage_web_port"),
        get_web_url=patch.object(charm, "_get_web_url", return_value="http://x:8080"),
    )
    patches.update(overrides)
    return patches


def test_update_status_restarts_on_detected_db_errors():
    """When the circuit breaker detects and auto-restarts for DB errors,
    the unit should still end up Active (transient, already handled)."""
    charm = _make_charm()
    charm.web_helper.is_running.return_value = True
    charm.web_helper.evaluate_db_health_circuit_breaker.return_value = "restarted"

    patches = _patch_common(charm)
    with patches["verify_installation"], patches["should_run_web"], patches[
        "should_run_worker"
    ], patches["get_url"], patches["get_mode"], patches["get_version"], patches[
        "manage_port"
    ], patches[
        "get_web_url"
    ]:
        charm._update_status()

    charm.web_helper.evaluate_db_health_circuit_breaker.assert_called_once()
    assert isinstance(charm.unit.status, CharmActiveStatus)


def test_update_status_blocks_when_circuit_open():
    """When the circuit breaker reports circuit_open (DB errors persisted
    through the automatic-restart budget), _update_status must set
    BlockedStatus so operators are alerted, instead of reporting
    ActiveStatus for a broken web unit."""
    charm = _make_charm()
    charm.web_helper.is_running.return_value = True
    charm.web_helper.evaluate_db_health_circuit_breaker.return_value = "circuit_open"

    patches = _patch_common(charm)
    with patches["verify_installation"], patches["should_run_web"], patches[
        "should_run_worker"
    ], patches["get_url"], patches["get_mode"], patches["get_version"], patches[
        "manage_port"
    ], patches[
        "get_web_url"
    ]:
        charm._update_status()

    charm.web_helper.evaluate_db_health_circuit_breaker.assert_called_once()
    assert isinstance(charm.unit.status, CharmBlockedStatus)
    assert "database" in charm.unit.status.message.lower()


def test_reset_db_health_action_calls_helper_reset():
    """The `reset-db-health` action must call
    web_helper.reset_db_health_circuit_breaker() and report success."""
    charm = _make_charm()
    event = MagicMock()

    charm._on_reset_db_health_action(event)

    charm.web_helper.reset_db_health_circuit_breaker.assert_called_once()
    event.set_results.assert_called_once()
