"""Helper for the cross-process idempotency tests in tests/test_spiderqueue.py.

Each invocation is one Scrapyd instance sharing a SQLite database directory with the others. It is run in a fresh
interpreter via subprocess, so that the tests do not fork a process that inherited a running Twisted reactor.
"""

import argparse
import json
import sys

from scrapyd.config import Config
from scrapyd.exceptions import IdempotencyConflictError
from scrapyd.spiderqueue import SqliteSpiderQueue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dbs_dir")
    parser.add_argument("project")
    parser.add_argument("idempotency_key")
    parser.add_argument("jobid")
    parser.add_argument("--arg", dest="arg_value", default=None)
    args = parser.parse_args()

    queue = SqliteSpiderQueue(Config(values={"dbs_dir": args.dbs_dir}), args.project)
    spider_args = {} if args.arg_value is None else {"arg1": args.arg_value}
    try:
        jobid = queue.add(
            "spider1",
            idempotency_key=args.idempotency_key,
            _job=args.jobid,
            settings={"A": "1"},
            **spider_args,
        )
    except IdempotencyConflictError:
        result = {"outcome": "conflict"}
    else:
        result = {"outcome": "ok", "jobid": jobid}

    # The parent reads stdout after the process exits.
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
