from typing import Optional

from dclib.models.base import BaseModel


class PhotovoltaicPerformanceModel(BaseModel):
    """
    Photovoltaic performance model in EnergyPlus. For more information, please refer to
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-electric-load-center-generator.html#photovoltaicperformanceequivalentone-diode
    """
    cell_type: Optional[str] = "CrystallineSilicon"
    number_of_cells_in_series: Optional[int] = 36
    active_area: Optional[float] = 1.0
    transmittance_absorptance_product: Optional[float] = 0.9
    semiconductor_band_gap: Optional[float] = 1.12
    shunt_resistance: Optional[float] = 1000000.0
    short_circuit_current: Optional[float] = 4.75
    open_circuit_voltage: Optional[float] = 21.4
    reference_temperature: Optional[float] = 25.0
    reference_irradiance: Optional[float] = 1000.0
    module_current_at_maximum_power: Optional[float] = 4.45
    module_voltage_at_maximum_power: Optional[float] = 17.0
    temperature_coefficient_of_short_circuit_current: Optional[float] = 0.00065
    temperature_coefficient_of_open_circuit_voltage: Optional[float] = -0.08
    nominal_operating_cell_temperature_test_ambient_temperature: Optional[float] = 20.0
    nominal_operating_cell_temperature_test_cell_temperature: Optional[float] = 47.0
    nominal_operating_cell_temperature_test_insolation: Optional[float] = 800.0
    module_heat_loss_coefficient: Optional[float] = 30.0
    total_heat_capacity: Optional[float] = 50000.0
