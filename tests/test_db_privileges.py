"""Unit tests for ConcourseWebHelper.ensure_db_privileges."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from concourse_web import ConcourseWebHelper

DB_URL = "postgresql://relation-9:pw@10.0.0.1:5432/concourse"


class FakeCursor:
    """Cursor returning canned rows and recording executed SQL."""

    def __init__(self, tables_exist: bool, missing_priv_results: list):
        self.sql: list = []
        self._tables_exist = tables_exist
        self._missing = list(missing_priv_results)
        self._row: list = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "information_schema.tables" in sql:
            self._row = [(self._tables_exist,)]
        elif "has_table_privilege" in sql:
            self._row = [(self._missing.pop(0),)]
        elif "session_user" in sql:
            self._row = [("relation-9",)]
        elif "tableowner" in sql:
            self._row = [("charmed_concourse_owner",)]
        elif "pg_roles r" in sql:
            self._row = [("charmed_concourse_dml",)]
        else:
            self._row = []

    def fetchone(self):
        return self._row[0]

    def fetchall(self):
        return self._row


def _run(cursor: FakeCursor) -> bool:
    psycopg2 = MagicMock()
    psycopg2.Error = Exception
    psycopg2.OperationalError = OSError
    psycopg2.connect.return_value.cursor.return_value = cursor
    with patch.dict(sys.modules, {"psycopg2": psycopg2}):
        return ConcourseWebHelper(MagicMock()).ensure_db_privileges(DB_URL)


def _grants(cursor: FakeCursor) -> list:
    return [s for s in cursor.sql if s.startswith("GRANT")]


def test_no_fix_when_all_tables_have_privileges():
    cur = FakeCursor(True, [False])
    assert _run(cur) is True
    assert _grants(cur) == []


def test_fix_when_any_table_lacks_privileges():
    # e.g. auth_request is fine but access_tokens (newer migration) is not
    cur = FakeCursor(True, [True, False])
    assert _run(cur) is True
    assert any('TO "charmed_concourse_dml"' in s for s in _grants(cur))
    assert any("SET ROLE" in s for s in cur.sql)


def test_fails_when_privileges_still_missing_after_fix():
    cur = FakeCursor(True, [True, True])
    assert _run(cur) is False


def test_skips_when_migrations_not_run():
    cur = FakeCursor(False, [])
    assert _run(cur) is True
    assert _grants(cur) == []
