from .config import TaskInstanceConfig


class TaskInstance(object):
    def __init__(self, task_name: str, task_instance_index, task_instance_config: TaskInstanceConfig):
        self.name = task_name + '-' + str(task_instance_index)
        self.task_instance_index = task_instance_index
        self.cpu = task_instance_config.cpu
        self.memory = task_instance_config.memory
        self.disk = task_instance_config.disk
        self.duration = task_instance_config.duration

        self.allocated_server_name = None  # each task instance is running on a machine
        self.started = False
        self.finished = False
        self.released = False
        self.start_time = None
        self.finish_time = None
        self.remaining_time = task_instance_config.duration  # remaining time to finish this task instance
        self.running_time = 0  # running time of this task instance
        self.running_time_at_one_slot = 0  # running time of this task instance at one specific slot
