"""Server object model in a data center
"""

from typing import Optional, OrderedDict, List
from pydantic import Field

from dclib.workloads.vm import TaskInstance, Task

from dclib.models.geometry.server import ServerGeometryModel
from dclib.models.cooling.server import ServerCoolingModel
from dclib.models.computing.server import ServerComputingModel
from dclib.models.power.server import ServerPowerModel

from dclib.models.base import BaseModel


class ServerGeometry(ServerGeometryModel):
    """
    Object that represents the geometry of a server.

    :param model: server model
    :param slot_position: the position of the slot in the rack
    :param orientation: the orientation of the server in the rack, 0 means the server is facing the front of the rack,
    90 means the server is facing the right side of the rack. The angle is defined in the counter-clockwise direction
    w.r.t your orientation.
    """
    model: Optional[str] = None
    slot_position: Optional[int] = 0
    orientation: Optional[float] = 0
    slot_height: Optional[float] = 0.05

    @property
    def height(self) -> float:
        """
        Get the height of the server. Here we assume that a server slot is 0.045 m height.
        :return:
        """
        return self.slot_occupation * self.slot_height

    @property
    def inlet_area(self) -> float:
        """
        Get the inlet area of the server. We assume the shape is a rectangle.
        :return:
        """
        return self.height * float(self.width)

    @property
    def outlet_area(self) -> float:
        """
        Get the outlet area of the server. We assume the shape is a rectangle.
        :return:
        """
        return self.height * float(self.width)


class ServerComputing(ServerComputingModel):
    """
    Object that represents the computing resource of a server. This object defines a common CPU server.
    Several methods to simulate the computing resource allocation and reallocation are provided.
    """
    model: Optional[str] = None
    remaining_cpu: Optional[float] = 64
    remaining_mem: Optional[float] = 128
    remaining_disk: Optional[float] = 1000
    task_instances: Optional[dict] = {}

    def accommodate(self, task: Task) -> bool:
        """
        Check whether this machine can accommodate a task.
        :param task:
        :return:
        """
        return self.remaining_cpu >= task.cpu and \
               self.remaining_mem >= task.mem and \
               self.remaining_disk >= task.disk

    def run_task_instance(self, task_instance: TaskInstance) -> None:
        """
        Run a task instance on this machine by occupying multiple resources in a non-preemptive way.
        :param task_instance: an instance of a task that specified the resource requirement
        :return:
        """
        self.remaining_cpu -= task_instance.cpu
        self.remaining_mem -= task_instance.memory
        self.remaining_disk -= task_instance.disk
        self.task_instances[task_instance.name] = task_instance

    def stop_task_instance(self, task_instance: TaskInstance) -> None:
        """
        Stop a task instance on this machine by releasing multiple resources.
        :param task_instance:
        :return:
        """
        self.remaining_cpu += task_instance.cpu
        self.remaining_mem += task_instance.memory
        self.remaining_disk += task_instance.disk
        del self.task_instances[task_instance.name]

    @property
    def running_task_instances(self) -> List:
        """
        Get all running task instances on this machine.
        :return:
        """
        ls = []
        for task_instance in self.task_instances:
            if task_instance.started and not task_instance.finished:
                ls.append(task_instance)
        return ls

    @property
    def finished_task_instances(self) -> List:
        """
        Get all finished task instances on this machine.
        :return:
        """
        ls = []
        for task_instance in self.task_instances:
            if task_instance.finished:
                ls.append(task_instance)
        return ls

    @property
    def capacity(self) -> List:
        """
        Get the capacity info of this machine.
        :return:
        """
        return [self.cpu_capacity, self.mem_capacity, self.disk_capacity]


class ServerCooling(ServerCoolingModel):
    """ Server cooling properties """
    model: Optional[str] = None


class ServerPower(ServerPowerModel):
    """ Server power properties
    """
    model: Optional[str] = None
    input_power: Optional[float] = ""  # unit(W)

    def calc(self, cpu: float = 0, mem: float = 0, disk: float = 0) -> float:
        return self.rated_cpu_power * cpu + self.rated_mem_power * mem + self.rated_disk_power * disk


class Server(BaseModel):
    uid: Optional[str] = ""
    valid_id: Optional[str] = None # Only letters, digits, and underscores are allowed. 
    geometry: Optional[ServerGeometry] = Field(default_factory=ServerGeometry)
    cooling: Optional[ServerCooling] = Field(default_factory=ServerCooling)
    power: Optional[ServerPower] = Field(default_factory=ServerPower)
    computing: Optional[ServerComputing] = Field(default_factory=ServerComputing)
    meta: Optional[OrderedDict] = Field(default_factory=dict)

    @property
    def k(self) -> float:
        """
        Get the k value of the server. This is the ratio of the pressure drop between the inlet and outlet. This value
        will be used in CFD simulation.
        :return:
        """
        tu = 0.1
        u = float(self.volume_flow_rate) / self.geometry.outlet_area
        k = 0.5 * (tu * u) ** 2
        return k

    @property
    def epsilon(self) -> float:
        """
        Get the epsilon value of the server. This is the ratio of the pressure drop between the inlet and outlet. This
        value will be used in CFD simulation.
        :return:
        """
        nu = 1.5e-05
        eddy_viscosity_ratio = 10
        return 0.09 * (self.k ** 2) / (nu * eddy_viscosity_ratio)

    @property
    def volume_flow_rate(self) -> float:
        """
        Get the volume flow rate of the server given its current server power consumption.
        :return:
        """
        if self.cooling.fan_type == "Fixed":
            assert self.cooling.volume_flow_rate is not None, \
                "Please specify the constant server volume flow rate."
            server_volume_flow_rate = self.cooling.volume_flow_rate
        elif self.cooling.fan_type == "Variable":
            assert self.cooling.volume_flow_rate_ratio is not None, \
                "Please specify the volume flow rate ratio in terms of input power."
            server_volume_flow_rate = self.cooling.volume_flow_rate_ratio * self.power.input_power
        else:
            raise ValueError("Invalid fan type.")

        return server_volume_flow_rate

    @volume_flow_rate.setter
    def volume_flow_rate(self, value: float):
        self.cooling.volume_flow_rate = value
        self.cooling.fan_type = "Fixed"

    @property
    def power_consumption(self) -> float:
        """
        Get the power consumption of the server. This is the sum of the power consumption of the computing resource
        :return:
        """
        res = 0
        for task_instance in self.computing.running_task_instances:
            cpu = task_instance.cpu
            memory = task_instance.memory
            disk = task_instance.disk
            cpu_utilization = cpu / self.computing.cpu_capacity
            memory_utilization = memory / self.computing.mem_capacity
            disk_utilization = disk / self.computing.disk_capacity
            res += self.power.calc(cpu_utilization, memory_utilization, disk_utilization)
        return res
