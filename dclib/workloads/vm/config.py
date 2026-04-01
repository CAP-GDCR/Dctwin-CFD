from typing import List


class TaskInstanceConfig:
    def __init__(self, task_config):
        self.cpu = task_config.cpu
        self.memory = task_config.memory
        self.disk = task_config.disk
        self.duration = task_config.duration


class TaskConfig:
    def __init__(
        self,
        task_id,
        num_instance: int,
        cpu: int,
        memory: float,
        duration: float,
        deadline: float,
        flavor_type: str = "",
        disk: float = 0.0,
    ):
        self.task_id = task_id
        self.num_instance = num_instance
        self.cpu = cpu
        self.memory = memory
        self.disk = disk
        self.duration = duration
        self.flavor_type = flavor_type
        self.deadline = deadline


class JobConfig:
    def __init__(self, job_id: str, submit_time: float, task_configs: List[TaskConfig]):
        self.submit_time = submit_time
        self.task_configs = task_configs
        self.id = job_id
