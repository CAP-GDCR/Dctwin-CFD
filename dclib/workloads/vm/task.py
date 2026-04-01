from .config import TaskInstanceConfig, TaskConfig
from .instance import TaskInstance


class Task:
    def __init__(self, job_name: str, task_config: TaskConfig):
        self.name = task_config.task_id
        self.job_name = job_name
        self.cpu = task_config.cpu
        self.mem = task_config.memory
        self.disk = task_config.disk
        self.duration = task_config.duration
        self.deadline = task_config.deadline
        self.slack_time = -1
        self.num_instances = task_config.num_instance
        self.flav_type = task_config.flavor_type
        self.submission_time = -1
        self.duration_estimation: float = -1
        self.duration_distribution = None
        self.started = False
        self.finished = False
        self.task_instances = []
        task_instance_config = TaskInstanceConfig(task_config)
        for task_instance_index in range(task_config.num_instance):
            self.task_instances.append(TaskInstance(self.name, task_instance_index, task_instance_config))
        self.next_instance_pointer = 0
