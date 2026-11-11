import datetime
import sqlite3

import pytest

from scrapyd.sqlite import JsonSqliteIdempotencyKeys, JsonSqlitePriorityQueue, SqliteFinishedJobs
from tests import get_finished_job


@pytest.fixture
def jsonsqlitepriorityqueue():
    return JsonSqlitePriorityQueue()


@pytest.fixture
def sqlitefinishedjobs():
    q = SqliteFinishedJobs(":memory:")
    q.add(get_finished_job("p1", "s1", "j1", end_time=datetime.datetime(2001, 2, 3, 4, 5, 6, 7)))
    q.add(get_finished_job("p2", "s2", "j2", end_time=datetime.datetime(2001, 2, 3, 4, 5, 6, 8)))
    q.add(get_finished_job("p3", "s3", "j3", end_time=datetime.datetime(2001, 2, 3, 4, 5, 6, 9)))
    return q


def test_jsonsqlitepriorityqueue_empty(jsonsqlitepriorityqueue):
    assert jsonsqlitepriorityqueue.pop() is None


def test_jsonsqlitepriorityqueue_one(jsonsqlitepriorityqueue):
    msg = "a message"
    jsonsqlitepriorityqueue.put(msg)

    assert "_id" not in msg
    assert jsonsqlitepriorityqueue.pop() == msg
    assert jsonsqlitepriorityqueue.pop() is None


def test_jsonsqlitepriorityqueue_multiple(jsonsqlitepriorityqueue):
    msg1 = "first message"
    msg2 = "second message"
    jsonsqlitepriorityqueue.put(msg1)
    jsonsqlitepriorityqueue.put(msg2)
    out = []
    out.append(jsonsqlitepriorityqueue.pop())
    out.append(jsonsqlitepriorityqueue.pop())

    assert msg1 in out
    assert msg2 in out
    assert jsonsqlitepriorityqueue.pop() is None


def test_jsonsqlitepriorityqueue_priority(jsonsqlitepriorityqueue):
    msg1 = "message 1"
    msg2 = "message 2"
    msg3 = "message 3"
    msg4 = "message 4"
    jsonsqlitepriorityqueue.put(msg1, priority=1.0)
    jsonsqlitepriorityqueue.put(msg2, priority=5.0)
    jsonsqlitepriorityqueue.put(msg3, priority=3.0)
    jsonsqlitepriorityqueue.put(msg4, priority=2.0)

    assert jsonsqlitepriorityqueue.pop() == msg2
    assert jsonsqlitepriorityqueue.pop() == msg3
    assert jsonsqlitepriorityqueue.pop() == msg4
    assert jsonsqlitepriorityqueue.pop() == msg1


def test_jsonsqlitepriorityqueue_iter_len_clear(jsonsqlitepriorityqueue):
    assert len(jsonsqlitepriorityqueue) == 0
    assert list(jsonsqlitepriorityqueue) == []

    msg1 = "message 1"
    msg2 = "message 2"
    msg3 = "message 3"
    msg4 = "message 4"
    jsonsqlitepriorityqueue.put(msg1, priority=1.0)
    jsonsqlitepriorityqueue.put(msg2, priority=5.0)
    jsonsqlitepriorityqueue.put(msg3, priority=3.0)
    jsonsqlitepriorityqueue.put(msg4, priority=2.0)

    assert len(jsonsqlitepriorityqueue) == 4
    assert list(jsonsqlitepriorityqueue) == [(msg2, 5.0), (msg3, 3.0), (msg4, 2.0), (msg1, 1.0)]

    jsonsqlitepriorityqueue.clear()

    assert len(jsonsqlitepriorityqueue) == 0
    assert list(jsonsqlitepriorityqueue) == []


def test_jsonsqlitepriorityqueue_remove(jsonsqlitepriorityqueue):
    assert len(jsonsqlitepriorityqueue) == 0
    assert list(jsonsqlitepriorityqueue) == []

    msg1 = "good message 1"
    msg2 = "bad message 2"
    msg3 = "good message 3"
    msg4 = "bad message 4"
    jsonsqlitepriorityqueue.put(msg1)
    jsonsqlitepriorityqueue.put(msg2)
    jsonsqlitepriorityqueue.put(msg3)
    jsonsqlitepriorityqueue.put(msg4)
    jsonsqlitepriorityqueue.remove(lambda x: x.startswith("bad"))

    assert list(jsonsqlitepriorityqueue) == [(msg1, 0.0), (msg3, 0.0)]


@pytest.mark.parametrize(
    "value",
    [
        "native ascii str",
        "\xa3",
        123,
        1.2,
        True,
        ["a", "list", 1],
        {"a": "dict"},
    ],
)
def test_jsonsqlitepriorityqueue_types(jsonsqlitepriorityqueue, value):
    jsonsqlitepriorityqueue.put(value)

    assert jsonsqlitepriorityqueue.pop() == value


def test_sqlitefinishedjobs_add(sqlitefinishedjobs):
    assert len(sqlitefinishedjobs) == 3


def test_sqlitefinishedjobs_clear_all(sqlitefinishedjobs):
    sqlitefinishedjobs.clear()

    assert len(sqlitefinishedjobs) == 0


def test_sqlitefinishedjobs_clear_keep_0(sqlitefinishedjobs):
    sqlitefinishedjobs.clear(finished_to_keep=0)

    assert len(sqlitefinishedjobs) == 0


def test_sqlitefinishedjobs_clear_keep_2(sqlitefinishedjobs):
    sqlitefinishedjobs.clear(finished_to_keep=2)

    assert len(sqlitefinishedjobs) == 2


def test_sqlitefinishedjobs__iter__(sqlitefinishedjobs):
    actual = list(sqlitefinishedjobs)

    assert (actual[0][0], actual[0][1]) == ("p3", "s3")
    assert (actual[1][0], actual[1][1]) == ("p2", "s2")
    assert (actual[2][0], actual[2][1]) == ("p1", "s1")


@pytest.fixture
def idempotencykeys():
    store = JsonSqliteIdempotencyKeys(":memory:")
    store.add("key1", "job1", "fingerprint1")
    store.add("key2", "job2", "fingerprint2")
    return store


def test_idempotency_keys_get_absent(idempotencykeys):
    assert idempotencykeys.get("absent") is None


def test_idempotency_keys_get_present(idempotencykeys):
    assert idempotencykeys.get("key1") == {"job": "job1", "fingerprint": "fingerprint1"}


def test_idempotency_keys_duplicate_raises(idempotencykeys):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        idempotencykeys.add("key1", "job3", "fingerprint3")

    # The failed insert did not change the existing row.
    assert idempotencykeys.get("key1") == {"job": "job1", "fingerprint": "fingerprint1"}


def test_idempotency_keys_len(idempotencykeys):
    assert len(idempotencykeys) == 2


def test_idempotency_keys_clear(idempotencykeys):
    idempotencykeys.clear()

    assert len(idempotencykeys) == 0
    assert idempotencykeys.get("key1") is None

    # The table still works after being cleared.
    idempotencykeys.add("key1", "job3", "fingerprint3")
    assert idempotencykeys.get("key1") == {"job": "job3", "fingerprint": "fingerprint3"}


def test_idempotency_keys_share_connection(tmp_path):
    queue = JsonSqlitePriorityQueue(str(tmp_path / "project.db"))
    queue.put({"name": "spider1"}, priority=0.0)

    store = JsonSqliteIdempotencyKeys(tmp_path / "project.db", conn=queue.conn)
    store.add("key1", "job1", "fingerprint1")

    # Both tables are reachable through the shared connection and committed together.
    assert len(queue) == 1
    assert store.get("key1") == {"job": "job1", "fingerprint": "fingerprint1"}


def test_idempotency_keys_creates_table_on_legacy_database(tmp_path):
    database = tmp_path / "project.db"
    queue = JsonSqlitePriorityQueue(str(database))
    queue.conn.close()

    # Reopening an existing database (whose queue table was created by an older Scrapyd) must add the table.
    store = JsonSqliteIdempotencyKeys(str(database))
    assert len(store) == 0
    store.add("key1", "job1", "fingerprint1")
    assert store.get("key1") == {"job": "job1", "fingerprint": "fingerprint1"}
