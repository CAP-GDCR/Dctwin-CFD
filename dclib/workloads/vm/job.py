from .config import JobConfig
from .task import Task


class Job:

    def __init__(self, job_config: JobConfig):
        self.id = job_config.id
        self.submission_time = job_config.submit_time
        self.waiting_tasks: dict[str: Task] = {}
        self.running_tasks: dict[str, Task] = {}
        self.started = False
        self.finished = False
        for task_config in job_config.task_configs:
            task_id = task_config.task_id
            self.waiting_tasks[task_id] = Task(self.id, task_config)
