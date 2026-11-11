from pathlib import Path

import pytest
import requests

from integration_tests import req

with (Path(__file__).absolute().parent.parent / "tests" / "fixtures" / "mybot.egg").open("rb") as f:
    EGG = f.read()


def assert_response(method, path, expected, **kwargs):
    response = req(method, path, **kwargs)
    data = response.json()
    data.pop("node_name")

    assert data == expected
    assert response.content.endswith(b"\n")


@pytest.mark.parametrize(
    ("method", "basename"),
    [
        ("GET", "daemonstatus"),
        ("POST", "addversion"),
        ("POST", "schedule"),
        ("POST", "cancel"),
        ("GET", "status"),
        ("GET", "listprojects"),
        ("GET", "listversions"),
        ("GET", "listspiders"),
        ("GET", "listjobs"),
        ("POST", "delversion"),
        ("POST", "delproject"),
    ],
)
def test_options(method, basename):
    response = requests.options(
        f"http://127.0.0.1:6800/{basename}.json",
        auth=("hello12345", "67890world"),
    )

    assert response.status_code == 204, f"204 != {response.status_code}"
    assert response.headers["Allow"] == f"OPTIONS, HEAD, {method}"
    assert response.content == b""


# ListSpiders, Schedule, Cancel, Status and ListJobs return "project '%b' not found" on directory traversal attempts.
# The egg storage (in get_project_list, called by get_spider_queues, called by QueuePoller, used by these webservices)
# would need to find a project like "../project" (which is impossible with the default eggstorage) to not error.
@pytest.mark.parametrize(
    ("method", "basename", "params"),
    [
        ("post", "addversion", {"version": "v", "egg": EGG}),
        ("get", "listversions", {}),
        ("post", "delversion", {"version": "v"}),
        ("post", "delproject", {}),
    ],
)
def test_project_directory_traversal(method, basename, params):
    response = getattr(requests, method)(
        f"http://127.0.0.1:6800/{basename}.json",
        auth=("hello12345", "67890world"),
        **{"params" if method == "get" else "data": {"project": "../p", **params}},
    )

    data = response.json()
    data.pop("node_name")

    assert response.status_code == 200, f"200 != {response.status_code}"
    assert data == {"status": "error", "message": "DirectoryTraversalError: ../p"}


def test_daemonstatus():
    assert_response("get", "/daemonstatus.json", {"status": "ok", "running": 0, "pending": 0, "finished": 0})


def test_schedule_nonexistent_project():
    assert_response(
        "post",
        "/schedule.json",
        {"status": "error", "message": "project 'nonexistent' not found"},
        data={"project": "nonexistent", "spider": "nospider"},
    )


def test_status_nonexistent_job():
    assert_response(
        "get",
        "/status.json",
        {"status": "ok", "currstate": None},
        params={"job": "sample"},
    )


def test_status_nonexistent_project():
    assert_response(
        "get",
        "/status.json",
        {"status": "error", "message": "project 'nonexistent' not found"},
        params={"job": "sample", "project": "nonexistent"},
    )


def test_cancel_nonexistent_project():
    assert_response(
        "post",
        "/cancel.json",
        {"status": "error", "message": "project 'nonexistent' not found"},
        data={"project": "nonexistent", "job": "nojob"},
    )


def test_listprojects():
    assert_response(
        "get",
        "/listprojects.json",
        {"status": "ok", "projects": []},
    )


def test_listversions():
    assert_response(
        "get",
        "/listversions.json",
        {"status": "ok", "versions": []},
        params={"project": "sample"},
    )


def test_listspiders_nonexistent_project():
    assert_response(
        "get",
        "/listspiders.json",
        {"status": "error", "message": "project 'nonexistent' not found"},
        params={"project": "nonexistent"},
    )


def test_listjobs():
    assert_response(
        "get",
        "/listjobs.json",
        {"status": "ok", "pending": [], "running": [], "finished": []},
    )


def test_listjobs_nonexistent_project():
    assert_response(
        "get",
        "/listjobs.json",
        {"status": "error", "message": "project 'nonexistent' not found"},
        params={"project": "nonexistent"},
    )


def test_delversion_nonexistent_project():
    assert_response(
        "post",
        "/delversion.json",
        {"status": "error", "message": "version 'nonexistent' not found"},
        data={"project": "sample", "version": "nonexistent"},
    )


def test_delproject_nonexistent_project():
    assert_response(
        "post",
        "/delproject.json",
        {"status": "error", "message": "project 'nonexistent' not found"},
        data={"project": "nonexistent"},
    )


def test_schedule_idempotency_key():
    project = "idempotency_project"
    req("post", "/addversion.json", data={"project": project, "version": "v1", "egg": EGG})

    base = {"project": project, "spider": "spider1", "idempotency_key": "key-1"}

    # The first request enqueues a job and registers the key.
    first = req("post", "/schedule.json", data=base).json()
    jobid = first["jobid"]
    assert first["status"] == "ok"

    # A replay returns the same jobid and does not enqueue a second job.
    replay = req("post", "/schedule.json", data=base).json()
    assert replay["jobid"] == jobid

    pending = req("get", "/listjobs.json", params={"project": project}).json()["pending"]
    assert len(pending) == 1
    assert pending[0]["id"] == jobid

    # Re-sending the key together with an explicit jobid is a bad request.
    bad = req(
        "post",
        "/schedule.json",
        status=400,
        data={**base, "jobid": "explicit"},
    )
    assert bad.json()["status"] == "error"
    assert len(req("get", "/listjobs.json", params={"project": project}).json()["pending"]) == 1

    # The same key with a different spider argument is a conflict.
    conflict = req(
        "post",
        "/schedule.json",
        status=409,
        data={**base, "arg1": "val1"},
    )
    data = conflict.json()
    assert data["status"] == "error"
    assert data["message"].startswith("idempotency_key 'key-1' was already used with different parameters")
    assert jobid in data["message"]

    # The original job is untouched and the key still resolves to it.
    pending = req("get", "/listjobs.json", params={"project": project}).json()["pending"]
    assert len(pending) == 1
    assert pending[0]["id"] == jobid
    assert req("post", "/schedule.json", data=base).json()["jobid"] == jobid

    # A different key enqueues a separate job.
    second = req("post", "/schedule.json", data={**base, "idempotency_key": "key-2"}).json()
    assert second["jobid"] != jobid
    assert len(req("get", "/listjobs.json", params={"project": project}).json()["pending"]) == 2

    req("post", "/delproject.json", data={"project": project})


def test_schedule_without_idempotency_key_unchanged():
    project = "idempotency_legacy_project"
    req("post", "/addversion.json", data={"project": project, "version": "v1", "egg": EGG})

    payload = {"project": project, "spider": "spider1"}
    first = req("post", "/schedule.json", data=payload).json()
    second = req("post", "/schedule.json", data=payload).json()

    assert first["jobid"] != second["jobid"]
    assert len(req("get", "/listjobs.json", params={"project": project}).json()["pending"]) == 2

    req("post", "/delproject.json", data={"project": project})
