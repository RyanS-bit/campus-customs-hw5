"""Allowlist SQLite authorizer for the restricted write connection (Problem 5, Stage 2).

Anything not listed is denied: no UPDATE, DELETE, schema change, ATTACH/DETACH (which also
blocks VACUUM INTO), PRAGMA, recursive CTE, or unlisted function. Inserts are allowed only into
the three tables agents may append to. Statements inside triggers are checked the same way.

The authorizer cannot see the implicit deletes of INSERT OR REPLACE; those are stopped by the
no-delete triggers together with PRAGMA recursive_triggers=ON (see ops.open_write_connection).
"""

import sqlite3

WRITABLE_TABLES = frozenset({("main", "pending_actions"), ("main", "drafts"), ("audit", "events")})
ALLOWED_FUNCTIONS = frozenset({"json_valid", "length", "coalesce", "count", "sum", "total", "max", "min", "round"})

ACTION_NAMES = {
    getattr(sqlite3, "SQLITE_" + n): n for n in (
        "CREATE_INDEX", "CREATE_TABLE", "CREATE_TEMP_INDEX", "CREATE_TEMP_TABLE", "CREATE_TEMP_TRIGGER",
        "CREATE_TEMP_VIEW", "CREATE_TRIGGER", "CREATE_VIEW", "DELETE", "DROP_INDEX", "DROP_TABLE",
        "DROP_TEMP_INDEX", "DROP_TEMP_TABLE", "DROP_TEMP_TRIGGER", "DROP_TEMP_VIEW", "DROP_TRIGGER",
        "DROP_VIEW", "INSERT", "PRAGMA", "READ", "SELECT", "TRANSACTION", "UPDATE", "ATTACH", "DETACH",
        "ALTER_TABLE", "REINDEX", "ANALYZE", "CREATE_VTABLE", "DROP_VTABLE", "FUNCTION", "SAVEPOINT", "RECURSIVE",
    ) if hasattr(sqlite3, "SQLITE_" + n)
}


class RestrictedConnection(sqlite3.Connection):
    """A connection that remembers every authorizer denial (for diagnostics and test evidence)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.denials: list[dict] = []


def install(con: RestrictedConnection) -> None:
    def authorizer(action, arg1, arg2, dbname, source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_INSERT and (dbname, arg1) in WRITABLE_TABLES:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and arg2 in ALLOWED_FUNCTIONS:
            return sqlite3.SQLITE_OK
        con.denials.append({"action": ACTION_NAMES.get(action, action), "target": arg1, "detail": arg2,
                            "database": dbname, "via_trigger": source})
        return sqlite3.SQLITE_DENY

    con.set_authorizer(authorizer)
