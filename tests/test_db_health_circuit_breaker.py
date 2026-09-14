#!/usr/bin/env python3
"""Regression tests for the DB-error auto-restart circuit breaker.

Background: after a PostgreSQL charm/snap refresh that does not change
connection endpoints/credentials, `concourse-server` can keep serving
requests over stale DB connections, causing
`SQLSTATE 42501 permission denied for table ...` errors and
"Internal Server Error / Failed to connect to the database" for users,
even though a fresh connection to the same database works fine.

`ConcourseWebHelper.evaluate_db_health_circuit_breaker()` detects this by
scanning recent `concourse-server` journal output for known DB-error
signatures and, if found, restarts the service — but only within a
bounded number of attempts with cooldown backoff, to avoid an infinite
restart loop when the database itself is genuinely broken.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from concourse_web import ConcourseWebHelper  # noqa: E402


def _make_helper(tmp_path):
    charm = MagicMock()
    charm.model.config = {}
    helper = ConcourseWebHelper(charm)
    # Redirect the circuit breaker's state file into the test tmpdir so
    # tests never touch the real /var/lib/concourse path.
    helper._db_health_state_file = tmp_path / "db-health-state.json"
    return helper


def test_no_recent_db_errors_keeps_service_untouched(tmp_path):
    """When no DB error signatures appear in recent logs, the breaker must
    not restart the service and must report a healthy status."""
    helper = _make_helper(tmp_path)

    with patch.object(
        helper, "check_recent_db_errors", return_value=False
    ), patch.object(helper, "restart_service") as restart:
        status = helper.evaluate_db_health_circuit_breaker()

    assert status == "healthy"
    restart.assert_not_called()


def test_first_db_error_triggers_one_restart(tmp_path):
    """The first time DB errors are detected, the breaker restarts the
    service exactly once and records the attempt."""
    helper = _make_helper(tmp_path)

    with patch.object(
        helper, "check_recent_db_errors", return_value=True
    ), patch.object(helper, "restart_service") as restart:
        status = helper.evaluate_db_health_circuit_breaker()

    assert status == "restarted"
    restart.assert_called_once()

    state = json.loads(helper._db_health_state_file.read_text())
    assert state["restart_count"] == 1
    assert state["last_restart"] is not None


def test_errors_within_cooldown_do_not_restart_again(tmp_path):
    """If DB errors are still detected shortly after a restart (within the
    cooldown window), the breaker must not restart again immediately —
    this is what prevents a tight restart loop."""
    helper = _make_helper(tmp_path)

    with patch.object(helper, "check_recent_db_errors", return_value=True), patch.object(
        helper, "restart_service"
    ) as restart:
        helper.evaluate_db_health_circuit_breaker()  # first restart
        restart.reset_mock()
        status = helper.evaluate_db_health_circuit_breaker()  # immediately again

    assert status == "cooldown"
    restart.assert_not_called()


def test_persistent_errors_open_circuit_after_max_restarts(tmp_path):
    """If DB errors persist across the maximum number of automatic
    restart attempts (even after cooldowns elapse), the breaker must stop
    restarting and open the circuit so a human can investigate."""
    helper = _make_helper(tmp_path)

    with patch.object(helper, "check_recent_db_errors", return_value=True), patch.object(
        helper, "restart_service"
    ) as restart:
        for attempt in range(1, helper.MAX_AUTO_RESTARTS + 1):
            # Force the cooldown to already have elapsed for each attempt.
            state = helper._load_db_health_state()
            state["last_restart"] = "2000-01-01T00:00:00+00:00"
            helper._save_db_health_state(state)
            status = helper.evaluate_db_health_circuit_breaker()
            assert status == "restarted"

        restart.reset_mock()
        # Force cooldown elapsed once more; breaker should now refuse to
        # restart because MAX_AUTO_RESTARTS has been reached.
        state = helper._load_db_health_state()
        state["last_restart"] = "2000-01-01T00:00:00+00:00"
        helper._save_db_health_state(state)
        status = helper.evaluate_db_health_circuit_breaker()

    assert status == "circuit_open"
    restart.assert_not_called()

    state = json.loads(helper._db_health_state_file.read_text())
    assert state["circuit_open"] is True


def test_circuit_open_state_persists_and_blocks_further_restarts(tmp_path):
    """Once the circuit is open, subsequent calls must keep reporting
    circuit_open (and never restart again) until explicitly reset."""
    helper = _make_helper(tmp_path)
    helper._save_db_health_state(
        {"restart_count": helper.MAX_AUTO_RESTARTS, "last_restart": None, "circuit_open": True}
    )

    with patch.object(
        helper, "check_recent_db_errors", return_value=True
    ), patch.object(helper, "restart_service") as restart:
        status = helper.evaluate_db_health_circuit_breaker()

    assert status == "circuit_open"
    restart.assert_not_called()


def test_recovery_resets_restart_count(tmp_path):
    """Once errors stop appearing, the breaker must reset its restart
    counter so a later unrelated incident gets the full retry budget."""
    helper = _make_helper(tmp_path)
    helper._save_db_health_state(
        {"restart_count": 2, "last_restart": "2000-01-01T00:00:00+00:00", "circuit_open": False}
    )

    with patch.object(
        helper, "check_recent_db_errors", return_value=False
    ), patch.object(helper, "restart_service") as restart:
        status = helper.evaluate_db_health_circuit_breaker()

    assert status == "healthy"
    restart.assert_not_called()
    state = json.loads(helper._db_health_state_file.read_text())
    assert state["restart_count"] == 0
    assert state["circuit_open"] is False


def test_reset_db_health_circuit_breaker_clears_state(tmp_path):
    """The manual reset (used by a `reset-db-health` action after a human
    fixes the underlying database problem) must clear the circuit so
    auto-restart can resume."""
    helper = _make_helper(tmp_path)
    helper._save_db_health_state(
        {"restart_count": helper.MAX_AUTO_RESTARTS, "last_restart": "2000-01-01T00:00:00+00:00", "circuit_open": True}
    )

    helper.reset_db_health_circuit_breaker()

    state = json.loads(helper._db_health_state_file.read_text())
    assert state == {"restart_count": 0, "last_restart": None, "circuit_open": False}


def test_check_recent_db_errors_detects_known_signatures(tmp_path):
    """check_recent_db_errors must return True when the journal contains
    any of the known DB-error signatures observed in the field."""
    helper = _make_helper(tmp_path)

    fake_result = MagicMock()
    fake_result.stdout = (
        "some line\n"
        'ERROR: permission denied for table workers (SQLSTATE 42501)\n'
        "another line\n"
    )
    with patch("concourse_web.subprocess.run", return_value=fake_result):
        assert helper.check_recent_db_errors() is True


def test_check_recent_db_errors_returns_false_when_clean(tmp_path):
    """check_recent_db_errors must return False when logs contain no
    known DB-error signatures."""
    helper = _make_helper(tmp_path)

    fake_result = MagicMock()
    fake_result.stdout = "atc.build-tracker.tracking\natc.web ready\n"
    with patch("concourse_web.subprocess.run", return_value=fake_result):
        assert helper.check_recent_db_errors() is False
