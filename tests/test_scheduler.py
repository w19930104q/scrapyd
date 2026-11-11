from pathlib import Path

import pytest
from zope.interface.verify import verifyObject

from scrapyd.config import Config
from scrapyd.exceptions import ScrapydError
from scrapyd.interfaces import ISpiderScheduler
from scrapyd.scheduler import SpiderScheduler
from scrapyd.utils import get_spider_queues


@pytest.fixture
def scheduler(tmpdir):
    eggs_dir = Path(tmpdir) / "eggs"
    dbs_dir = Path(tmpdir) / "dbs"
    config = Config(values={"eggs_dir": str(eggs_dir), "dbs_dir": str(dbs_dir)})
    (eggs_dir / "mybot1").mkdir(parents=True)
    (eggs_dir / "mybot2").mkdir(parents=True)
    return SpiderScheduler(config)


def test_interface(scheduler):
    verifyObject(ISpiderScheduler, scheduler)


# Need sorted(), because os.listdir() in FilesystemEggStorage.list_projects() uses an arbitrary order.
def test_list_projects_update_projects(scheduler):
    assert sorted(scheduler.list_projects()) == ["mybot1", "mybot2"]

    (Path(scheduler.config.get("eggs_dir")) / "settings_raise").mkdir(parents=True)

    assert sorted(scheduler.list_projects()) == ["mybot1", "mybot2"]

    scheduler.update_projects()

    assert sorted(scheduler.list_projects()) == ["mybot1", "mybot2", "settings_raise"]


def test_schedule(scheduler):
    queues = get_spider_queues(scheduler.config)
    mybot1_queue = queues["mybot1"]
    mybot2_queue = queues["mybot2"]

    assert not mybot1_queue.count()
    assert not mybot2_queue.count()

    scheduler.schedule("mybot1", "myspider1", 2, a="b")
    scheduler.schedule("mybot2", "myspider2", 1, c="d")
    scheduler.schedule("mybot2", "myspider3", 10, e="f")

    assert mybot1_queue.pop() == {"name": "myspider1", "a": "b"}
    assert mybot2_queue.pop() == {"name": "myspider3", "e": "f"}
    assert mybot2_queue.pop() == {"name": "myspider2", "c": "d"}


def test_schedule_returns_jobid(scheduler):
    assert scheduler.schedule("mybot1", "myspider1", _job="the-job") == "the-job"


def test_schedule_idempotent(scheduler):
    queue = scheduler.queues["mybot1"]

    jobid1 = scheduler.schedule(
        "mybot1", "myspider1", idempotency_key="key-1", _job="job-1", settings={}, arg1="v1"
    )
    jobid2 = scheduler.schedule(
        "mybot1", "myspider1", idempotency_key="key-1", _job="job-2", settings={}, arg1="v1"
    )

    assert jobid1 == jobid2 == "job-1"
    assert queue.count() == 1
    assert len(queue.idempotency_keys) == 1


class QueueWithoutIdempotency:
    """A minimal custom ISpiderQueue that does not implement the optional idempotency methods."""

    def __init__(self, config, project):
        self.messages = []

    def add(self, name, priority=0.0, **spider_args):
        message = spider_args.copy()
        message["name"] = name
        self.messages.append(message)
        return spider_args.get("_job")

    def pop(self):
        return self.messages.pop() if self.messages else None

    def count(self):
        return len(self.messages)

    def list(self):
        return list(self.messages)

    def remove(self, func):
        before = len(self.messages)
        self.messages = [message for message in self.messages if not func(message)]
        return before - len(self.messages)

    def clear(self):
        self.messages.clear()


def test_schedule_idempotency_unsupported_queue(tmp_path, monkeypatch):
    eggs_dir = tmp_path / "eggs"
    dbs_dir = tmp_path / "dbs"
    (eggs_dir / "mybot1").mkdir(parents=True)
    config = Config(
        values={
            "eggs_dir": str(eggs_dir),
            "dbs_dir": str(dbs_dir),
            "spiderqueue": "tests.test_scheduler.QueueWithoutIdempotency",
        }
    )

    scheduler = SpiderScheduler(config)
    # Without a key, scheduling works as before.
    assert scheduler.schedule("mybot1", "spider1", _job="job1") == "job1"

    # With a key, the limitation is surfaced instead of silently ignored.
    with pytest.raises(ScrapydError, match="does not support the idempotency_key parameter"):
        scheduler.schedule("mybot1", "spider1", idempotency_key="key-1", _job="job2")

    assert scheduler.queues["mybot1"].count() == 1


def test_update_projects_clears_keys_of_removed_project(tmp_path):
    eggs_dir = tmp_path / "eggs"
    dbs_dir = tmp_path / "dbs"
    (eggs_dir / "mybot1").mkdir(parents=True)
    (eggs_dir / "mybot2").mkdir(parents=True)
    config = Config(values={"eggs_dir": str(eggs_dir), "dbs_dir": str(dbs_dir)})

    scheduler = SpiderScheduler(config)
    scheduler.schedule("mybot1", "spider1", idempotency_key="key-1", _job="job-1", settings={})
    old_queue = scheduler.queues["mybot1"]
    assert len(old_queue.idempotency_keys) == 1

    # The project disappears from egg storage (as on delproject.json).
    (eggs_dir / "mybot1").rmdir()
    scheduler.update_projects()

    assert "mybot1" not in scheduler.queues
    assert len(old_queue.idempotency_keys) == 0
    # Other projects' keys are untouched.
    scheduler.schedule("mybot2", "spider1", idempotency_key="key-2", _job="job-2", settings={})
    assert len(scheduler.queues["mybot2"].idempotency_keys) == 1
