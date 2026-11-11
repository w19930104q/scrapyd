import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from twisted.internet.defer import inlineCallbacks, maybeDeferred
from zope.interface.verify import verifyObject

from scrapyd.config import Config
from scrapyd.exceptions import IdempotencyConflictError
from scrapyd.interfaces import ISpiderQueue
from scrapyd.spiderqueue import SqliteSpiderQueue, make_fingerprint

spider_args = {
    "arg1": "val1",
    "arg2": 2,
    "arg3": "\N{SNOWMAN}",
}
expected = spider_args.copy()
expected["name"] = "spider1"


@pytest.fixture
def spiderqueue():
    return SqliteSpiderQueue(Config(values={"dbs_dir": ":memory:"}), "mybot")


def test_interface(spiderqueue):
    verifyObject(ISpiderQueue, spiderqueue)


@inlineCallbacks
def test_pop(spiderqueue):
    yield maybeDeferred(spiderqueue.add, "spider0", 5)
    yield maybeDeferred(spiderqueue.add, "spider1", 10, **spider_args)
    yield maybeDeferred(spiderqueue.add, "spider1", 0)

    assert (yield maybeDeferred(spiderqueue.count)) == 3

    assert (yield maybeDeferred(spiderqueue.pop)) == expected

    assert (yield maybeDeferred(spiderqueue.count)) == 2


@inlineCallbacks
def test_list(spiderqueue):
    assert (yield maybeDeferred(spiderqueue.list)) == []

    yield maybeDeferred(spiderqueue.add, "spider1", 10, **spider_args)
    yield maybeDeferred(spiderqueue.add, "spider1", 10, **spider_args)

    assert (yield maybeDeferred(spiderqueue.list)) == [expected, expected]


@inlineCallbacks
def test_remove(spiderqueue):
    yield maybeDeferred(spiderqueue.add, "spider0", 5)
    yield maybeDeferred(spiderqueue.add, "spider1", 10, **spider_args)
    yield maybeDeferred(spiderqueue.add, "spider1", 0)

    assert (yield maybeDeferred(spiderqueue.count)) == 3

    assert (yield maybeDeferred(spiderqueue.remove, lambda message: message["name"] == "spider1")) == 2

    assert (yield maybeDeferred(spiderqueue.count)) == 1


@inlineCallbacks
def test_clear(spiderqueue):
    assert (yield maybeDeferred(spiderqueue.count)) == 0

    yield maybeDeferred(spiderqueue.add, "spider1", 10, **spider_args)
    yield maybeDeferred(spiderqueue.add, "spider1", 10, **spider_args)

    assert (yield maybeDeferred(spiderqueue.count)) == 2

    yield maybeDeferred(spiderqueue.clear)

    assert (yield maybeDeferred(spiderqueue.count)) == 0


def add_idempotent(queue, key="key", jobid="job1", **kwargs):
    kwargs.setdefault("settings", {})
    return queue.add("spider1", idempotency_key=key, _job=jobid, **kwargs)


def test_add_returns_jobid_without_key(spiderqueue):
    assert spiderqueue.add("spider1", _job="j1") == "j1"
    assert spiderqueue.add("spider1") is None


def test_idempotent_replay_returns_first_jobid(spiderqueue):
    jobid1 = add_idempotent(spiderqueue)
    jobid2 = add_idempotent(spiderqueue, jobid="job2")
    jobid3 = add_idempotent(spiderqueue, jobid="job3")

    assert jobid1 == jobid2 == jobid3 == "job1"
    assert spiderqueue.count() == 1
    assert len(spiderqueue.idempotency_keys) == 1
    assert spiderqueue.list()[0]["_job"] == "job1"
    # The idempotency key is not stored in the queued message.
    assert "idempotency_key" not in spiderqueue.list()[0]


def test_idempotent_distinct_keys_enqueue_distinct_jobs(spiderqueue):
    assert add_idempotent(spiderqueue, key="key1", jobid="job1") == "job1"
    assert add_idempotent(spiderqueue, key="key2", jobid="job2") == "job2"

    assert spiderqueue.count() == 2
    assert len(spiderqueue.idempotency_keys) == 2


def test_idempotent_keys_are_project_scoped(tmp_path):
    config = Config(values={"dbs_dir": str(tmp_path / "dbs")})
    queue_a = SqliteSpiderQueue(config, "projectA")
    queue_b = SqliteSpiderQueue(config, "projectB")

    # The same key string in two projects maps to two independent jobs.
    assert queue_a.add("spider1", idempotency_key="K", _job="a1", settings={}) == "a1"
    assert queue_b.add("spider1", idempotency_key="K", _job="b1", settings={}) == "b1"

    # A conflict in one project does not affect the other.
    queue_b.add("spider1", idempotency_key="K2", _job="b2", settings={})
    with pytest.raises(IdempotencyConflictError):
        queue_b.add("spider1", idempotency_key="K2", _job="b3", settings={}, arg1="v")
    assert queue_a.add("spider1", idempotency_key="K", _job="x", settings={}) == "a1"
    assert queue_b.add("spider1", idempotency_key="K", _job="x", settings={}) == "b1"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Different spider.
        ({"name": "spider1"}, {"name": "spider2"}),
        # Different version.
        ({"_version": "r1"}, {"_version": "r2"}),
        # Different priority.
        ({"priority": 0.0}, {"priority": 1.0}),
        # Different setting value.
        ({"settings": {"A": "1"}}, {"settings": {"A": "2"}}),
        # Different setting key.
        ({"settings": {"A": "1"}}, {"settings": {"B": "1"}}),
        # Different spider argument.
        ({"arg1": "val1"}, {"arg1": "val2"}),
        # An added spider argument.
        ({"arg1": "val1"}, {"arg1": "val1", "arg2": "val2"}),
    ],
)
def test_idempotent_conflict(spiderqueue, first, second):
    name = first.pop("name", "spider1")
    first.setdefault("settings", {})
    spiderqueue.add(name, idempotency_key="key", _job="job1", **first)

    with pytest.raises(IdempotencyConflictError):
        spiderqueue.add(second.pop("name", name), idempotency_key="key", _job="job2", **second)

    # The conflict neither enqueues a job nor changes the first job.
    assert spiderqueue.count() == 1
    assert len(spiderqueue.idempotency_keys) == 1
    assert spiderqueue.list()[0]["_job"] == "job1"
    # The key is still usable with the original parameters.
    assert spiderqueue.add(name, idempotency_key="key", _job="job3", **first) == "job1"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Settings sent in a different order.
        ({"settings": {"A": "1", "B": "2"}}, {"settings": {"B": "2", "A": "1"}}),
        # Spider arguments sent in a different order.
        ({"arg1": "v1", "arg2": "v2"}, {"arg2": "v2", "arg1": "v1"}),
        # Omitted priority is equivalent to priority 0 (int or float).
        ({"priority": 0}, {}),
        ({"priority": 0.0}, {}),
    ],
)
def test_idempotent_equivalent_parameters(spiderqueue, first, second):
    assert spiderqueue.add("spider1", idempotency_key="key", _job="job1", **first) == "job1"
    assert spiderqueue.add("spider1", idempotency_key="key", _job="job2", **second) == "job1"
    assert spiderqueue.count() == 1


def test_make_fingerprint_stable_and_distinct():
    fp1 = make_fingerprint("s", 0.0, {"A": "1"}, {"a": "b"})
    fp2 = make_fingerprint("s", 0, {"A": "1"}, {"a": "b"})
    fp3 = make_fingerprint("s", 1.0, {"A": "1"}, {"a": "b"})

    assert fp1 == fp2
    assert fp1 != fp3


def test_idempotent_restart(tmp_path):
    config = Config(values={"dbs_dir": str(tmp_path / "dbs")})

    queue = SqliteSpiderQueue(config, "mybot")
    assert queue.add("spider1", idempotency_key="key", _job="job1", settings={}, arg1="v1") == "job1"

    # A new queue object (as created on Scrapyd restart) over the same database.
    reopened = SqliteSpiderQueue(config, "mybot")
    assert reopened.count() == 1
    assert reopened.add("spider1", idempotency_key="key", _job="job2", settings={}, arg1="v1") == "job1"
    assert reopened.count() == 1

    with pytest.raises(IdempotencyConflictError):
        reopened.add("spider1", idempotency_key="key", _job="job3", settings={}, arg1="changed")


def test_idempotent_survives_queue_operations(spiderqueue):
    add_idempotent(spiderqueue)
    add_idempotent(spiderqueue, key="key2", jobid="job2", arg1="other")

    # Pop and remove do not delete idempotency keys.
    spiderqueue.pop()
    spiderqueue.remove(lambda message: True)
    spiderqueue.clear()

    assert spiderqueue.count() == 0
    assert len(spiderqueue.idempotency_keys) == 2
    # Replays still resolve to the original job IDs.
    assert add_idempotent(spiderqueue, jobid="new") == "job1"
    assert add_idempotent(spiderqueue, key="key2", jobid="new2", arg1="other") == "job2"
    assert spiderqueue.count() == 0


def test_clear_idempotency_keys(spiderqueue):
    add_idempotent(spiderqueue)
    assert len(spiderqueue.idempotency_keys) == 1

    spiderqueue.clear_idempotency_keys()

    assert len(spiderqueue.idempotency_keys) == 0
    # After cleanup, the key can be registered again, enqueuing a new job.
    assert add_idempotent(spiderqueue, jobid="job2") == "job2"
    assert spiderqueue.count() == 2


def test_legacy_database_compatible(tmp_path):
    dbs_dir = tmp_path / "dbs"
    dbs_dir.mkdir()
    database = dbs_dir / "mybot.db"

    # A database as created by an older Scrapyd version: only the queue table exists.
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE spider_queue (id integer PRIMARY KEY, priority real key, message blob)")
    connection.commit()
    connection.close()

    queue = SqliteSpiderQueue(Config(values={"dbs_dir": str(dbs_dir)}), "mybot")

    assert queue.count() == 0
    assert add_idempotent(queue) == "job1"
    assert queue.count() == 1
    assert len(queue.idempotency_keys) == 1


class FailingConnection(sqlite3.Connection):
    fail_queue_insert = False
    fail_key_insert = False

    def execute(self, sql, *args, **kwargs):
        if self.fail_queue_insert and sql.startswith("INSERT INTO spider_queue"):
            raise RuntimeError("injected queue insert failure")
        if self.fail_key_insert and sql.startswith("INSERT INTO idempotency_keys"):
            raise RuntimeError("injected key insert failure")
        return super().execute(sql, *args, **kwargs)


def use_failing_connection(queue, *, fail_queue_insert=False, fail_key_insert=False):
    queue.q.conn.close()
    connection = FailingConnection(queue.q.database, check_same_thread=False)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.fail_queue_insert = fail_queue_insert
    connection.fail_key_insert = fail_key_insert
    queue.q.conn = connection
    queue.idempotency_keys.conn = connection
    return connection


def test_idempotent_failure_leaves_no_half_commit(tmp_path):
    config = Config(values={"dbs_dir": str(tmp_path / "dbs")})

    queue = SqliteSpiderQueue(config, "mybot")
    use_failing_connection(queue, fail_queue_insert=True)
    with pytest.raises(RuntimeError, match="injected queue insert failure"):
        add_idempotent(queue)
    assert queue.count() == 0
    assert len(queue.idempotency_keys) == 0

    queue2 = SqliteSpiderQueue(config, "mybot2")
    use_failing_connection(queue2, fail_key_insert=True)
    with pytest.raises(RuntimeError, match="injected key insert failure"):
        add_idempotent(queue2)
    assert queue2.count() == 0
    assert len(queue2.idempotency_keys) == 0

    # A healthy connection on each database can still schedule.
    assert add_idempotent(SqliteSpiderQueue(config, "mybot")) == "job1"
    assert add_idempotent(SqliteSpiderQueue(config, "mybot2")) == "job1"


def _spawn_idempotency_worker(dbs_dir, index, arg_value=None):
    worker = Path(__file__).with_name("idempotency_worker.py")
    command = [
        sys.executable,
        str(worker),
        str(dbs_dir),
        "mybot",
        "shared",
        f"job{index}",
    ]
    if arg_value is not None:
        command.extend(["--arg", arg_value])
    # A clean environment with the repository on PYTHONPATH, so that the helper imports the tested Scrapyd.
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )


def _wait_for_workers(processes):
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout))
    return results


def test_idempotent_concurrent_processes(tmp_path):
    dbs_dir = tmp_path / "dbs"
    processes = [_spawn_idempotency_worker(dbs_dir, index) for index in range(6)]
    results = _wait_for_workers(processes)

    # All processes see the same single jobid; which process wins the write lock is not deterministic.
    jobids = {result["jobid"] for result in results}
    assert len(jobids) == 1
    assert next(iter(jobids)).startswith("job")
    queue = SqliteSpiderQueue(Config(values={"dbs_dir": str(dbs_dir)}), "mybot")
    assert queue.count() == 1
    assert len(queue.idempotency_keys) == 1


def test_idempotent_concurrent_conflicting_processes(tmp_path):
    dbs_dir = tmp_path / "dbs"
    processes = [
        _spawn_idempotency_worker(dbs_dir, index, arg_value=f"v{index}") for index in range(6)
    ]
    results = _wait_for_workers(processes)

    assert len([result for result in results if result["outcome"] == "ok"]) == 1
    assert len([result for result in results if result["outcome"] == "conflict"]) == 5
    queue = SqliteSpiderQueue(Config(values={"dbs_dir": str(dbs_dir)}), "mybot")
    assert queue.count() == 1
    assert len(queue.idempotency_keys) == 1


def test_idempotent_concurrent_threads(tmp_path):
    # Separate connections (as used by two Scrapyd instances) sharing one database file, driven concurrently.
    dbs_dir = tmp_path / "dbs"
    errors = []
    jobids = []

    def worker(index):
        try:
            queue = SqliteSpiderQueue(Config(values={"dbs_dir": str(dbs_dir)}), "mybot")
            jobids.append(
                queue.add(
                    "spider1", idempotency_key="shared", _job=f"job{index}", settings={"A": "1"}, arg1="v1"
                )
            )
        except Exception as e:  # noqa: BLE001 the test fails with the actual error below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a worker thread deadlocked on the database lock"

    assert not errors
    assert len(set(jobids)) == 1
    queue = SqliteSpiderQueue(Config(values={"dbs_dir": str(dbs_dir)}), "mybot")
    assert queue.count() == 1
    assert len(queue.idempotency_keys) == 1
