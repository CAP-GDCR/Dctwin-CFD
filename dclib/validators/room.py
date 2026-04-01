from typing import OrderedDict
from loguru import logger

from pydantic import ValidationInfo

from dclib.models import Models
from dclib.data import Inputs
from dclib.room import Room, RoomConstruction, RoomStateControl, HeatGains

from dclib.ite.composite import ITE
from dclib.construction.entities.validators import BoxValidator
from dclib.ite.racks.validators import RackValidator
from dclib.ite.servers.validators import ServerValidator
from dclib.ite.racks.validators import RackValidator, RowValidator
from dclib.cooling.room.validators import ACUValidator
from dclib.cooling.room.validators import DehumidifierValidator
from dclib.cooling.room.validators import SensorValidator
from dclib.ite.composite import ITEValidator
from dclib.electrical.room.validators import LightValidator, ElectricEquipmentValidator, PeopleValidator
from dclib.cooling.room.validators.liquid_loops import LiquidFlowLoopsValidator


class RoomValidator:
    """ A class to validate zone-related aspects of a building """

    @classmethod
    def validate(cls, rooms: OrderedDict[str, Room], values: ValidationInfo) -> None:
        """ Validate the zones of the building """
        for room_name, room in rooms.items():
            room.uid = room_name if room.uid == "" else room.uid
            room.name = room_name
            cls.validate_room_constructions(
                room_construction=room.constructions,
                room_name=room.name,
                models=values.data["models"],
                inputs=values.data["inputs"],
            )
            cls._validate_thermostats(room_name, room.control_states)
        for room_name, room in rooms.items():
            cls._validate_heat_gains_ite(room_name, room)
            cls._validate_number_of_acu_ite(room)

    @classmethod
    def _validate_heat_gains_ite(cls, room_name: str, room: Room) -> None:
        """ 
        Validate the heat gains of the zone.
        Use default ITE model parameters if provided,
        otherwise assign ITE model parameters according to the nearest racks.
        """
        if room.constructions.num_ser > 0 \
            and (room.constructions.heat_gains is None or room.constructions.heat_gains.ites is None):
            logger.warning(f"Servers are defined in Room: '{room_name}', but ITE heat gains are not defined.")
            logger.debug("Building ITE heat gains from server configuration with default ITE model parameters.")
            logger.debug(f"There are {room.constructions.num_acu} ACU in Room: '{room_name}',"
                            f" attempting to build {room.constructions.num_acu} ITE heat gain models according to rack distances.")
            logger.debug(f"If you want to build ITE heat gain models with other design parameters, please define"
                            f" them separately in 'constructions.heat_gains'.")
            # wattsPerUnit = room.constructions.total_server_rated_power / room.constructions.num_ser
            # num_servers_per_acu = room.constructions.num_ser / room.constructions.num_acu
            # logger.debug(f"ITE model parameters: wattsPerUnit = {wattsPerUnit} W,"
            #              f" numberOfUnits = {int(num_servers_per_acu)}")
            ite2rack = room.constructions.ite2rack(room_name)
            ites = {}
            for idx, (ite_name, ite) in enumerate(ite2rack.items()):
                total_watts = 0
                number_of_units = 0
                for rack_name in ite["racks"]:
                    rack = room.constructions.racks[rack_name]
                    for server_name, server in rack.constructions.servers.items():
                        total_watts += server.power.rated_power
                        number_of_units += 1
                watts_per_unit = 0 if number_of_units == 0 else total_watts / number_of_units
                ites[ite_name] = ITE(
                    uid=f"{ite_name}",
                    wattsPerUnit=watts_per_unit,
                    numberOfUnits=number_of_units,
                    zone=room_name,
                    supplyAirNodeName=f"{room_name} inlet node {idx + 1}",
                )
            room.constructions.heat_gains = HeatGains(ites=ites)

    @classmethod
    def _validate_number_of_acu_ite(cls, room: Room) -> None:
        if room.constructions.acus is not None \
            and room.constructions.heat_gains is not None \
                and room.constructions.heat_gains.ites is not None:
            if room.constructions.num_acu != len(room.constructions.heat_gains.ites):
                raise ValueError(
                    f"Number of ACU {room.constructions.num_acu} does not equal to number of ITE heat gains "
                    f"{len(room.constructions.heat_gains.ites)} in Room: '{room.name}'"
            )

    @classmethod
    def _validate_thermostats(
        cls,
        room_name: str,
        control_states: RoomStateControl,
    ) -> None:
        assert control_states.thermostats.cooling_setpoint is not None, f"Cooling setpoint for {room_name} must be defined!"
        assert control_states.thermostats.heating_setpoint is not None, f"Heating setpoint for {room_name} must be defined!"
        #TODO add humidity setpoint validation if necessary

    @classmethod
    def validate_room_constructions(
        cls,
        room_construction: RoomConstruction,
        room_name: str,
        models: Models,
        inputs: Inputs,
    ) -> RoomConstruction:
        if room_construction.acus is not None:
            ACUValidator.validate(
                acus=room_construction.acus,
                geometry_models=models.geometry_models.acus,
                cooling_models=models.cooling_models.acus,
                power_models=models.power_models.acus,
                control_models=models.control_models.acus,
                curve_models=models.curve_models.acus,
                inputs=inputs.acus,
                context="room",
            )
        if room_construction.dehumidifiers is not None:
            DehumidifierValidator.validate(
                dehumidifiers=room_construction.dehumidifiers,
                geometry_models=models.geometry_models.dehumidifiers,
                cooling_models=models.cooling_models.dehumidifiers,
                power_models=models.power_models.dehumidifiers,
                control_models=models.control_models.dehumidifiers,
                curve_models=models.curve_models.dehumidifiers,
                inputs=inputs.dehumidifiers
            )
        if room_construction.boxes is not None:
            BoxValidator.validate(
                boxes=room_construction.boxes,
                geometry_models=models.geometry_models.boxes,
            )
        if room_construction.liquid_flow_networks is not None:
            LiquidFlowLoopsValidator.validate(
                loops=room_construction.liquid_flow_networks,
                models=models,
                inputs=inputs,
            )
        if room_construction.racks is not None:
            RackValidator.validate(
                racks=room_construction.racks,
                models=models,
                inputs=inputs.servers
            )
        if room_construction.rows is not None:
            RowValidator.validate(
                rows=room_construction.rows,
                models=models,
                inputs=inputs.servers
            )
        if room_construction.sensors is not None:
            SensorValidator.validate(
                sensors=room_construction.sensors,
            )
        if room_construction.heat_gains is not None:
            if room_construction.heat_gains.ites is not None:
                ITEValidator.validate(
                    ites=room_construction.heat_gains.ites,
                    ite_models=models.heat_gain_models.ite,
                    curve_models=models.curve_models.ites
                )
            if room_construction.heat_gains.electric_equipment is not None:
                ElectricEquipmentValidator.validate(
                    electric_equipment=room_construction.heat_gains.electric_equipment,
                    models=models.heat_gain_models.electric_equipment
                )
            if room_construction.heat_gains.light is not None:
                LightValidator.validate(
                    light=room_construction.heat_gains.light,
                    models=models.heat_gain_models.light
                )
            if room_construction.heat_gains.people is not None:
                PeopleValidator.validate(
                    people=room_construction.heat_gains.people,
                    models=models.heat_gain_models.people,
                )
            if room_construction.heat_gains.ites is not None and room_construction.num_ser > 0:
                if room_construction.total_server_rated_power != room_construction.total_ite_power:
                    logger.warning(
                        f"Total server rated power {room_construction.total_server_rated_power} W does not equal to total ITE power "
                        f"{room_construction.total_ite_power} W in Room {room_name}"
                    )
        return room_construction
