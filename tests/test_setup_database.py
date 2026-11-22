from pathlib import Path

import pytest

from scripts.setup_database import SetupError, _migration_files, setup_database


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=None, prepare=None):
        if "current_database()" in query:
            self.row = self.state
        elif "SELECT EXISTS" in query:
            self.row = (False,)

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, state):
        self.state = state
        self.cursor_calls = 0

    def cursor(self):
        self.cursor_calls += 1
        return FakeCursor(self.state)

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def transaction(self):
        return self._Transaction()

    def commit(self):
        pass


def test_migration_files_are_sorted_by_filename(tmp_path: Path):
    (tmp_path / "002_later.sql").write_text("select 2;", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("select 1;", encoding="utf-8")

    assert [path.name for path in _migration_files(tmp_path)] == [
        "001_first.sql",
        "002_later.sql",
    ]


def test_setup_refuses_untracked_existing_docqa_schema(tmp_path: Path):
    connection = FakeConnection(("existing", False, True, True))

    with pytest.raises(SetupError, match="already exist"):
        setup_database(connection, "postgres", tmp_path, assume_yes=True)


def test_generic_postgres_setup_rejects_existing_auth_schema(tmp_path: Path):
    connection = FakeConnection(("existing", False, False, True))

    with pytest.raises(SetupError, match="fresh PostgreSQL database"):
        setup_database(connection, "postgres", tmp_path, assume_yes=True)
