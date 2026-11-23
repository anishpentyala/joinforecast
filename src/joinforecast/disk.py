"""Opt-in private temporary-disk profiles; cleanup is not secure erasure."""

import json
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from . import InputError, _read_csv


def _storage_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except (OSError, sqlite3.Error) as error:
        raise InputError("Temporary profile storage: " + str(error)) from error


class _DiskCounts(Mapping):
    def __init__(self, connection):
        self.connection = connection
        self.size = 0
        self.closed = False

    def _execute(self, statement, parameters=()):
        if self.closed:
            raise InputError("Disk profile is closed; use it inside its context")
        try:
            return self.connection.execute(statement, parameters)
        except sqlite3.Error as error:
            raise InputError("Temporary profile storage: " + str(error)) from error

    @staticmethod
    def _encode(key):
        # Canonical array encoding is injective over tuples of strings and None.
        return json.dumps(key, ensure_ascii=True, separators=(",", ":"))

    def __getitem__(self, key):
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value

    def __iter__(self):
        for key, _ in self.items():
            yield key

    def get(self, key, default=None):
        row = self._execute("SELECT n FROM counts WHERE key = ?", (self._encode(key),)).fetchone()
        return row[0] if row is not None else default

    def __setitem__(self, key, count):
        encoded = self._encode(key)
        cursor = self._execute(
            "INSERT OR IGNORE INTO counts(key, n) VALUES (?, ?)", (encoded, count)
        )
        if cursor.rowcount:
            self.size += 1
        else:
            self._execute("UPDATE counts SET n = ? WHERE key = ?", (count, encoded))

    def _increment(self, key):
        # Parser-only single increment; do not change generic mapping writes.
        # No RETURNING clause, to retain compatibility with older SQLite.
        encoded = self._encode(key)
        cursor = self._execute("INSERT OR IGNORE INTO counts(key, n) VALUES (?, 1)", (encoded,))
        if cursor.rowcount:
            self.size += 1
        else:
            self._execute("UPDATE counts SET n = n + 1 WHERE key = ?", (encoded,))

    def __len__(self):
        if self.closed:
            raise InputError("Disk profile is closed; use it inside its context")
        return self.size

    def items(self):
        # Preserve first-seen order, including stable top-contributor ties.
        cursor = self._execute("SELECT key, n FROM counts ORDER BY rowid")
        try:
            for encoded, count in cursor:
                yield tuple(json.loads(encoded)), count
        except sqlite3.Error as error:
            raise InputError("Temporary profile storage: " + str(error)) from error
        finally:
            # The context may have closed the database while this generator was
            # suspended. Do not replace the wrapped iteration error on resume.
            if not self.closed:
                _storage_call(cursor.close)


@contextmanager
def sqlite_csv(
    path,
    columns,
    *,
    null_values=("",),
    delimiter=",",
    max_rows=10000000,
    max_keys=1000000,
    temp_dir=None,
):
    """Yield a Profile whose frequencies live in a private temporary database.

    Use only inside this context. Key values are written to disk, so choose a
    suitable temp_dir for sensitive data. Normal exit and errors clean up the
    database, not securely erase it. Forced termination can leave files behind.
    Cache is bounded, not total process memory; long CSV fields still cost RAM.
    Input limits have the same meaning as read_csv. No existing database is used.
    """
    temporary = _storage_call(TemporaryDirectory, prefix="joinforecast-", dir=temp_dir)
    connection = counts = None
    try:
        database = Path(temporary.name) / "profile.sqlite3"
        connection = _storage_call(sqlite3.connect, str(database))
        counts = _DiskCounts(connection)
        _storage_call(database.chmod, 0o600)
        counts._execute("PRAGMA cache_size = -8192")
        counts._execute("PRAGMA temp_store = FILE")
        counts._execute("CREATE TABLE counts(key TEXT PRIMARY KEY, n INTEGER NOT NULL)")
        # One transaction avoids a filesystem sync per input row.
        counts._execute("BEGIN")
        profile = _read_csv(
            path,
            columns,
            counts,
            null_values,
            delimiter,
            max_rows,
            max_keys,
            increment=counts._increment,
        )
        # Do not reinterpret an exception from the caller's with-body as storage
        # failure. Only our own database and filesystem operations are wrapped.
        yield profile
    finally:
        try:
            if counts is not None:
                counts.closed = True
            if connection is not None:
                _storage_call(connection.close)
        finally:
            _storage_call(temporary.cleanup)
