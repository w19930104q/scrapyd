import hashlib
import json

from zope.interface import implementer

from scrapyd import sqlite
from scrapyd.exceptions import IdempotencyConflictError
from scrapyd.interfaces import ISpiderQueue

# Message keys that are not spider arguments, and thus participate in the idempotency fingerprint alongside the
# spider name, priority and settings.
_RESERVED_KEYS = frozenset(("name", "_job", "_version", "settings"))


def make_fingerprint(name, priority, settings, spider_args):
    """Return a stable hash of the parameters that define a schedule request.

    The parameters are serialized with sorted keys, so the order in which ``setting`` and spider arguments are sent
    does not matter. Values are serialized as parsed by the webservice, so equivalent defaults (for example, an
    omitted ``priority`` and ``priority=0``) produce the same fingerprint.

    ``spider_args`` may include the reserved ``_job``, ``_version`` and ``settings`` keys. The job ID is excluded
    (the idempotency key maps to it instead), while the version and settings are included.
    """
    encoded = json.dumps(
        {
            "name": name,
            # A missing version (the latest version by default) is equivalent to _version=None.
            "version": spider_args.get("_version"),
            # The webservice parses priority as a float; normalize it, so that an omitted priority and priority=0
            # (and float/int values that compare equal) produce the same fingerprint.
            "priority": float(priority),
            "settings": dict(sorted(settings.items())),
            "args": dict(
                sorted(
                    (key, value)
                    for key, value in spider_args.items()
                    if key not in _RESERVED_KEYS
                )
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@implementer(ISpiderQueue)
class SqliteSpiderQueue:
    def __init__(self, config, project, table="spider_queue"):
        self.q = sqlite.initialize(sqlite.JsonSqlitePriorityQueue, config, project, table)
        # Share the queue's connection and database, so that the idempotency key and the queued job are committed in
        # one transaction.
        self.idempotency_keys = sqlite.JsonSqliteIdempotencyKeys(
            self.q.database, conn=self.q.conn
        )

    def add(self, name, priority=0.0, idempotency_key=None, **spider_args):
        if idempotency_key is None:
            message = spider_args.copy()
            message["name"] = name
            self.q.put(message, priority=priority)
            return spider_args.get("_job")

        return self.add_idempotent(name, priority, idempotency_key, spider_args)

    def add_idempotent(self, name, priority, idempotency_key, spider_args):
        settings = spider_args.get("settings", {})
        fingerprint = make_fingerprint(name, priority, settings, spider_args)
        conn = self.q.conn

        # BEGIN IMMEDIATE acquires the write lock before reading, so two Scrapyd instances sharing the database
        # cannot both observe an absent key and both enqueue the job.
        conn.execute("BEGIN IMMEDIATE")
        conflict = False
        try:
            existing = self.idempotency_keys.get(idempotency_key)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    conflict = True
                else:
                    conn.rollback()
                    return existing["job"]
            else:
                message = spider_args.copy()
                message["name"] = name
                conn.execute(
                    f"INSERT INTO {self.q.table} (priority, message) VALUES (?, ?)",
                    (priority, self.q.encode(message)),
                )
                self.idempotency_keys.add(idempotency_key, spider_args["_job"], fingerprint)
        except Exception:
            # Never leave the key registered without the queued job (or vice versa).
            conn.rollback()
            raise
        else:
            if conflict:
                conn.rollback()
                raise IdempotencyConflictError(
                    f"idempotency_key '{idempotency_key}' was already used with different parameters "
                    f"(existing job: {existing['job']})"
                )
            conn.commit()

        return spider_args["_job"]

    def pop(self):
        return self.q.pop()

    def count(self):
        return len(self.q)

    def list(self):
        return [message for message, _ in self.q]

    def remove(self, func):
        return self.q.remove(func)

    def clear(self):
        self.q.clear()

    def clear_idempotency_keys(self):
        """Delete all idempotency keys of the project. Called when the project is deleted."""
        self.idempotency_keys.clear()
