import abc
import numpy as np

from dclib.room import Room
from dclib.cooling.room.facilities.acu import ACU
from dclib.ite.servers.server import Server

from dctwin.utils.const import rho_air, air_specific_heat


class Boundary(abc.ABC):
    """A class to generate the boundary condition of the foam simulation"""

    zero_gradient = """
    {
        type            zeroGradient;
    }"""
    no_slip = """
    {
        type            noSlip;
    }"""

    @property
    @abc.abstractmethod
    def T(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def U(self) -> str:
        pass


class RoomBoundary(Boundary):
    def __init__(self, room: Room) -> None:
        self.room = room

    def generate_boundary(self, type_define) -> str:
        ceiling = f"ceiling_1 {type_define}"
        if self.room.constructions.false_ceiling is None:
            ceiling = ""

        floor = f"floor_1 {type_define}"
        if self.room.constructions.raised_floor is None:
            floor = ""

        boxes_types_index = {}
        boxes_name_list = []
        for box in self.room.constructions.boxes.values():
            if box.geometry.model not in boxes_types_index:
                boxes_types_index[box.geometry.model] = 1
            else:
                boxes_types_index[box.geometry.model] += 1
            boxes_name_list.append(
                f"box_{box.geometry.model}_{boxes_types_index[box.geometry.model]}"
            )
        boxes_boundary = "\n".join(
            [f"{box_name} {type_define}" for box_name in boxes_name_list]
        )

        rack_boundary = "\n".join(
            [
                f"rack_wall_{key} {type_define}"
                for key, rack in self.room.constructions.racks.items()
            ]
        )
        rack_with_panel = []
        for key, rack in self.room.constructions.racks.items():
            if rack.geometry.hasBlankingPanel:
                rack_with_panel.append(key)

        rack_panel_boundary = "\n".join(
            [f"rack_panel_{key} {type_define}" for key in rack_with_panel]
        )
        return f"""
        room_wall_1 {type_define}
        {ceiling}
        {floor}
        {boxes_boundary}
        {rack_boundary}
        {rack_panel_boundary}"""

    @property
    def T(self) -> str:
        return self.generate_boundary(self.zero_gradient)

    @property
    def U(self) -> str:
        return self.generate_boundary(self.no_slip)


class ACUBoundary(Boundary):
    def __init__(self, acu: ACU, init_temperature: float = 293.15) -> None:
        self.acu_id = acu.valid_id
        self.object = acu
        self.init_temperature = init_temperature
        self.supply_kelvin = round(
            acu.cooling.operating.supply_air_temperature + 273.15, 2
        )
        self.supply_air_volume_flow_rate = round(
            acu.cooling.operating.supply_air_volume_flow_rate, 6
        )
        self.supply_air_mass_flow_rate = rho_air * self.supply_air_volume_flow_rate
        self.cooling_capacity = round(acu.cooling.cooling_capacity, 6)  # unit: kW

    @property
    def p_rgh(self) -> str:
        return f"""
    acu_return_{self.acu_id}
    {{
        type        fixedValue;
        value       uniform 0;
    }}"""

    @property
    def T(self) -> str:
        t_sink = f"tSink_{self.acu_id}"
        if np.isclose(self.supply_air_volume_flow_rate, 0):
            outlet = self.zero_gradient
        else:
            outlet = f"""
    {{
        type            uniformFixedValue;
        value           uniform {self.init_temperature};
        uniformValue    
        {{
            type            expression;
            expression       "max(0,t2)";
            variables
            (
                "{t_sink}{{acu_return_{self.acu_id}}} = weightAverage(T)"
                "coolingCapacity = {self.cooling_capacity}"		
                "supplyAirMassFlowRate = {self.supply_air_mass_flow_rate}"
                "t1 = {t_sink} - (coolingCapacity * 1000 / (supplyAirMassFlowRate * {air_specific_heat}))"
                "t2 = {self.supply_kelvin}"
            );
        }};
    }}"""
        return f"""
    acu_wall_{self.acu_id} {self.zero_gradient}
    
    acu_supply_{self.acu_id} {outlet}
    
    acu_return_{self.acu_id} {self.zero_gradient}"""

    @property
    def U(self) -> str:
        if np.isclose(self.supply_air_volume_flow_rate, 0):
            supply = self.no_slip
            _return = self.no_slip
        else:
            supply = f"""
    {{
        type                flowRateInletVelocity;
        volumetricFlowRate  {self.supply_air_volume_flow_rate};
    }}"""

        _return = f"""
    {{
        type                flowRateOutletVelocity;
        volumetricFlowRate  {self.supply_air_volume_flow_rate};
    }}"""

        return f"""
    acu_wall_{self.acu_id} {self.no_slip}
    
    acu_supply_{self.acu_id} {supply}
    
    acu_return_{self.acu_id} {_return}"""


class ServerBoundary(Boundary):
    def __init__(self, server: Server, init_temperature: float = 293.15) -> None:
        self.server_id = server.valid_id
        self.object: Server = server
        self.init_temperature = init_temperature
        self.input_power = server.power.input_power
        self.server_volume_flow_rate = round(server.volume_flow_rate, 6)
        self.server_mass_flow_rate = rho_air * self.server_volume_flow_rate

    @property
    def T(self) -> str:
        t_sink = f"tSink_{self.server_id}"
        if np.isclose(self.server_mass_flow_rate, 0):
            outlet = self.zero_gradient
        else:
            value = f"{t_sink}+{self.input_power / (self.server_mass_flow_rate * air_specific_heat)}"
            outlet = f"""
    {{
        type            uniformFixedValue;
        value           uniform {self.init_temperature};
        uniformValue
        {{
            type            expression;
            expression       "{value}";
            variables
            (
                "{t_sink}{{server_inlet_{self.server_id}}} = weightAverage(T)"
            );
            }};
    }}"""
        return f"""
    server_inlet_{self.server_id} {self.zero_gradient}
            
    server_outlet_{self.server_id} {outlet}"""

    @property
    def U(self) -> str:
        if np.isclose(self.server_mass_flow_rate, 0):
            inlet = self.no_slip
            outlet = self.no_slip
        else:
            inlet = f"""
    {{
        type                flowRateOutletVelocity;
        volumetricFlowRate  {self.server_volume_flow_rate};
    }}"""

            outlet = f"""
    {{
        type                flowRateInletVelocity;
        volumetricFlowRate  {self.server_volume_flow_rate};
    }}"""
        return f"""
    server_inlet_{self.server_id} {inlet}
    
    server_outlet_{self.server_id} {outlet}"""

    @property
    def p_rgh(self) -> str:
        return f"""
    server_inlet_{self.server_id}
    {{
        type        fixedValue;
        value       uniform 0;
    }}"""
