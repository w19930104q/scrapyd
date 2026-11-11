from zope.interface import implementer

from scrapyd.exceptions import ScrapydError
from scrapyd.interfaces import ISpiderScheduler
from scrapyd.utils import get_spider_queues


@implementer(ISpiderScheduler)
class SpiderScheduler:
    def __init__(self, config):
        self.config = config
        self.queues = {}
        self.update_projects()

    def schedule(self, project, spider_name, priority=0.0, idempotency_key=None, **spider_args):
        queue = self.queues[project]
        if idempotency_key is not None and not hasattr(queue, "add_idempotent"):
            raise ScrapydError(
                f"The configured spider queue {type(queue).__name__} does not support the idempotency_key parameter"
            )
        return queue.add(spider_name, priority=priority, idempotency_key=idempotency_key, **spider_args)

    def list_projects(self):
        return list(self.queues)

    def update_projects(self):
        new_queues = get_spider_queues(self.config)

        # A project's idempotency keys are bound to the project for its whole life and are removed only when the
        # project itself is deleted (for example, by the delproject.json webservice).
        for project in set(self.queues) - set(new_queues):
            queue = self.queues[project]
            if hasattr(queue, "clear_idempotency_keys"):
                queue.clear_idempotency_keys()

        self.queues = new_queues
