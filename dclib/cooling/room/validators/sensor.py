from typing import Dict


class SensorValidator:
    """ A class to validate zone-related aspects of a building """

    @classmethod
    def validate(cls, sensors: Dict) -> None:
        for sensor_id, sensor in sensors.items():
            cls._validate_id(sensor_id)

    @classmethod
    def _validate_id(cls, _id: str) -> None:
        pass
