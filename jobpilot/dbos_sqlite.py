"""Compatibility for pinned DBOS timestamp defaults on SQLite older than 3.42."""

import sqlite3
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def sqlite_subsecond_clock(connection, record):
    if not isinstance(connection, sqlite3.Connection) or sqlite3.sqlite_version_info >= (3, 42):
        return

    def unixepoch(*args):
        if args == ("subsec",):
            return time.time()
        # Preserve the normal SQLite date/modifier contract using its built-in strftime.
        placeholders = ",".join("?" for _ in args)
        query = "SELECT strftime('%s'" + ("," + placeholders if args else "") + ")"
        value = connection.execute(query, args).fetchone()[0]
        return int(value) if value is not None else None

    connection.create_function("unixepoch", -1, unixepoch)
