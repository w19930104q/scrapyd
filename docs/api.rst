API
===

If :ref:`basic authentication<username>` is enabled, you can use ``curl``'s ``-u`` option in the examples below, for example:

.. code-block:: shell

   curl -u yourusername:yourpassword http://localhost:6800/daemonstatus.json

.. _daemonstatus.json:

daemonstatus.json
-----------------

.. versionadded:: 1.2.0

To check the load status of a service.

Supported request methods
  ``GET``

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/daemonstatus.json
   {"node_name": "mynodename", "status": "ok", "pending": 0, "running": 0, "finished": 0}

.. _addversion.json:

addversion.json
---------------

Add a version to a project in :ref:`eggstorage`, creating the project if needed.

Supported request methods
  ``POST``
Parameters
  ``project`` (required)
    the project name
  ``version`` (required)
    the project version

    Scrapyd uses the packaging `Version <https://packaging.pypa.io/en/stable/version.html>`__ to interpret the version numbers you provide.
  ``egg`` (required)
    a Python egg containing the project's code

    The egg must set an entry point to its Scrapy settings. For example, with a ``setup.py`` file:

    .. code-block:: python
       :emphasize-lines: 5

       setup(
           name         = 'project',
           version      = '1.0',
           packages     = find_packages(),
           entry_points = {'scrapy': ['settings = projectname.settings']},
       )

    Do this easily with the ``scrapyd-deploy`` command from the `scrapyd-client <https://github.com/scrapy/scrapyd-client>`__ package.

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/addversion.json -F project=myproject -F version=r23 -F egg=@myproject.egg
   {"node_name": "mynodename", "status": "ok", "spiders": 3}

.. _schedule.json:

schedule.json
-------------

Schedule a job. (A job is a `Scrapy crawl <https://docs.scrapy.org/en/latest/topics/commands.html#crawl>`__.)

If the :ref:`logs_dir` setting is set, log files are written to ``{logs_dir}/{project}/{spider}/{jobid}.log``. Set the ``jobid`` parameter to configure the basename of the log file.

.. important:: Like Scrapy's ``scrapy.Spider`` class, spiders should allow an arbitrary number of keyword arguments in their ``__init__`` method, because Scrapyd sets internally-generated spider arguments when starting crawls.

Supported request methods
  ``POST``
Parameters
  ``project`` (required)
    the project name
  ``spider`` (required)
    the spider name
  ``_version``
    the project version (the latest project version by default)
  ``jobid``
    the job's ID (a hexadecimal UUID v1 by default)
  ``priority``
    the job's priority in the project's spider queue (0 by default, higher number, higher priority)
  ``setting``
    a Scrapy setting

    For example, using `DOWNLOAD_DELAY <http://doc.scrapy.org/en/latest/topics/settings.html#download-delay>`__:

    .. code-block:: shell

       curl http://localhost:6800/schedule.json -d setting=DOWNLOAD_DELAY=2 -d project=myproject -d spider=somespider
  ``idempotency_key``
    an optional key that makes the request idempotent, so that retrying a request (for example, after an upstream
    timeout) does not start a second crawl

    When the key is used for the first time, the job is enqueued as usual, and the key is stored together with the
    job's parameters. Every later request that carries the same key (and the same project) is answered with the
    first request's ``jobid`` without enqueuing another job, whether the first job is still pending, running, has
    finished or was canceled.

    The parameters of every request carrying a key must be equivalent to those of the first request. The compared
    parameters are the spider, the ``_version``, the ``priority``, all ``setting`` values and all spider arguments.
    Equivalent means:

    -  the order of ``setting`` and spider arguments does not matter
    -  an omitted parameter and its default value (for example, an omitted ``priority`` and ``priority=0``) are
       equivalent

    If the parameters differ, Scrapyd answers with HTTP status code ``409 Conflict`` and leaves the original job
    untouched. If ``idempotency_key`` is sent together with an explicit ``jobid``, Scrapyd answers with HTTP status
    code ``400 Bad Request``. When ``idempotency_key`` is omitted, the request behaves exactly as before and every
    request enqueues a new job.

    The key is scoped to the project and kept for the whole life of the project: it is not removed when the job
    leaves the queue, finishes or is cleaned up by :ref:`finished_to_keep`. It is removed only when the project
    itself is deleted via :ref:`delproject.json`. With the default SQLite :ref:`spiderqueue`, the key and the queued
    job are committed in a single transaction, so two Scrapyd instances sharing the same database accept the key
    only once, even under concurrent requests. Databases created by older Scrapyd versions are upgraded
    automatically.
  Any other parameter
    a spider argument

    For example, using ``arg1``:

    .. code-block:: shell

       curl http://localhost:6800/schedule.json -d arg1=val1 -d project=myproject -d spider=somespider

    .. warning::

       When such parameters are set multiple times, only the first value is sent to the spider.

       To change this behavior, please `open an issue <https://github.com/scrapy/scrapyd/issues>`__.

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/schedule.json -d project=myproject -d spider=somespider
   {"node_name": "mynodename", "status": "ok", "jobid": "6487ec79947edab326d6db28a2d86511e8247444"}

Idempotent scheduling example:

.. code-block:: shell-session

   $ # The first request enqueues a job and registers the key.
   $ curl http://localhost:6800/schedule.json -d project=myproject -d spider=somespider -d idempotency_key=order-123
   {"node_name": "mynodename", "status": "ok", "jobid": "6487ec79947edab326d6db28a2d86511e8247444"}
   $ # A retried request with the same key and equivalent parameters returns the same jobid, without a new crawl.
   $ curl http://localhost:6800/schedule.json -d project=myproject -d spider=somespider -d idempotency_key=order-123
   {"node_name": "mynodename", "status": "ok", "jobid": "6487ec79947edab326d6db28a2d86511e8247444"}
   $ # A request with the same key but different parameters is rejected.
   $ curl http://localhost:6800/schedule.json -d project=myproject -d spider=somespider -d idempotency_key=order-123 -d arg1=val1
   {"node_name": "mynodename", "status": "error", "message": "idempotency_key 'order-123' was already used with different parameters (existing job: 6487ec79947edab326d6db28a2d86511e8247444)"}

.. _status.json:

status.json
-----------

.. versionadded:: 1.5.0

Get the status of a job.

Supported request methods
  ``GET``
Parameters
  ``job`` (required)
    the job ID
  ``project``
    the project name

The job's status is indicated by the ``currstate`` key whose value is one of ``'pending'``, ``'running'`` or ``'finished'`` – or ``None`` if the job is not found.

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/status.json?job=6487ec79947edab326d6db28a2d86511e8247444
   {"node_name": "mynodename", "status": "ok", "currstate": "running"}

.. _cancel.json:

cancel.json
-----------

Cancel a job.

-  If the job is pending, it is removed from the project's spider queue.
-  If the job is running, the process is sent a signal to terminate.

Supported request methods
  ``POST``
Parameters
  ``project`` (required)
    the project name
  ``job`` (required)
    the job ID
  ``signal``
    the `signal <https://docs.python.org/3/library/signal.html#module-contents>`__ to send to the Scrapy process (``BREAK`` by default on Windows and ``INT`` by default, otherwise), one of:

    -  an integer
    -  `HUP <https://docs.python.org/3/library/signal.html#signal.SIGHUP>`__
    -  `INT <https://docs.python.org/3/library/signal.html#signal.SIGINT>`__
    -  `KILL <https://docs.python.org/3/library/signal.html#signal.SIGKILL>`__
    -  STOP
    -  `TERM <https://docs.python.org/3/library/signal.html#signal.SIGTERM>`__

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/cancel.json -d project=myproject -d job=6487ec79947edab326d6db28a2d86511e8247444
   {"node_name": "mynodename", "status": "ok", "prevstate": "running"}

.. _listprojects.json:

listprojects.json
-----------------

Get the projects.

Supported request methods
  ``GET``

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/listprojects.json
   {"node_name": "mynodename", "status": "ok", "projects": ["myproject", "otherproject"]}

.. _listversions.json:

listversions.json
-----------------

Get the versions of a project in :ref:`eggstorage`, in :ref:`order<overview-order>`, with the latest version last.

Supported request methods
  ``GET``
Parameters
  ``project`` (required)
    the project name

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/listversions.json?project=myproject
   {"node_name": "mynodename", "status": "ok", "versions": ["r99", "r156"]}

.. _listspiders.json:

listspiders.json
----------------

Get the spiders in a version of a project.

.. note:: If the project is configured via a :ref:`scrapy.cfg<config-settings>` file rather than uploaded via the :ref:`addversion.json` webservice, don't set the ``version`` parameter.

Supported request methods
  ``GET``
Parameters
  ``project`` (required)
    the project name
  ``_version``
    the project version (the latest project version by default)

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/listspiders.json?project=myproject
   {"node_name": "mynodename", "status": "ok", "spiders": ["spider1", "spider2", "spider3"]}

.. _listjobs.json:

listjobs.json
-------------

Get the pending, running and finished jobs of a project.

-  Pending jobs are in :ref:`spider queues<spiderqueue>`.
-  Running jobs have Scrapy processes.
-  Finished jobs are in :ref:job storage<jobstorage>`.

   .. note::

      -  The default :ref:`jobstorage` setting stores jobs in memory, such that jobs are lost when the Scrapyd process ends.
      -  ``log_url`` is ``null`` in the response if :ref:`logs_dir` is disabled or the file doesn't exist.
      -  ``items_url`` is ``null`` in the response if :ref:`items_dir` is disabled or the file doesn't exist.

Supported request methods
  ``GET``
Parameters
  ``project``
    filter results by project name

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/listjobs.json?project=myproject | python -m json.tool
   {
       "node_name": "mynodename",
       "status": "ok",
       "pending": [
           {
               "id": "78391cc0fcaf11e1b0090800272a6d06",
               "project": "myproject",
               "spider": "spider1",
               "version": "0.1",
               "settings": {"DOWNLOAD_DELAY=2"},
               "args": {"arg1": "val1"},
           }
       ],
       "running": [
           {
               "id": "422e608f9f28cef127b3d5ef93fe9399",
               "project": "myproject",
               "spider": "spider2",
               "pid": 93956,
               "start_time": "2012-09-12 10:14:03.594664",
               "log_url": "/logs/myproject/spider3/2f16646cfcaf11e1b0090800272a6d06.log",
               "items_url": "/items/myproject/spider3/2f16646cfcaf11e1b0090800272a6d06.jl"
           }
       ],
       "finished": [
           {
               "id": "2f16646cfcaf11e1b0090800272a6d06",
               "project": "myproject",
               "spider": "spider3",
               "start_time": "2012-09-12 10:14:03.594664",
               "end_time": "2012-09-12 10:24:03.594664",
               "log_url": "/logs/myproject/spider3/2f16646cfcaf11e1b0090800272a6d06.log",
               "items_url": "/items/myproject/spider3/2f16646cfcaf11e1b0090800272a6d06.jl"
           }
       ]
   }

.. _delversion.json:

delversion.json
---------------

Delete a version of a project from :ref:`eggstorage`. If no versions of the project remain, delete the project, too.

Supported request methods
  ``POST``
Parameters
  ``project`` (required)
    the project name
  ``version`` (required)
    the project version

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/delversion.json -d project=myproject -d version=r99
   {"node_name": "mynodename", "status": "ok"}

.. _delproject.json:

delproject.json
---------------

Delete a project and its versions from :ref:`eggstorage`.

Supported request methods
  ``POST``
Parameters
  ``project`` (required)
      the project name

Example:

.. code-block:: shell-session

   $ curl http://localhost:6800/delproject.json -d project=myproject
   {"node_name": "mynodename", "status": "ok"}
