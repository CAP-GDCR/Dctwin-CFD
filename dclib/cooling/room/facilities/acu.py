""" Air Conditioning Unit (ACU) Component
"""

from typing import Optional, OrderedDict, Any
from pydantic import Field

from dclib.models.base import BaseModel
from dclib.models.geometry import Face, Vertex, ACUGeometryModel
from dclib.models.cooling import ACUCoolingModel
from dclib.models.power import ACUPowerModel
from dclib.models.control import ACUControlModel


class ACUOutdoorAir(BaseModel):
    """ ACU outdoor air properties
    """
    uid: Optional[str] = ""


class ACUGeometry(ACUGeometryModel):
    """ ACU geometry properties
    """
    model: Optional[str] = None
    orientation: int = 0
    location: Vertex = None

    @property
    def supply_area(self) -> float:
        return self.supply_face.width * self.supply_face.length

    @property
    def return_area(self) -> float:
        return self.return_face.width * self.return_face.length


class ACUCoolingOperating(BaseModel):
    """ ACU cooling operating properties """
    supply_air_temperature: Optional[float] = None  # unit(C)
    supply_air_volume_flow_rate: Optional[float] = None  # unit(m3/s)


class ACUCooling(ACUCoolingModel):
    """ ACU cooling properties """
    model: Optional[str] = None
    oa: ACUOutdoorAir = Field(default_factory=ACUOutdoorAir)
    operating: ACUCoolingOperating = Field(default_factory=ACUCoolingOperating)


class ACUPowerOperating(BaseModel):
    """ ACU power operating properties """
    power: Optional[float] = 1000  # unit(W)


class ACUPower(ACUPowerModel):
    """ ACU power properties """
    model: Optional[str] = None
    operating: ACUPowerOperating = Field(default_factory=ACUPowerOperating)


class ACUController(ACUControlModel):
    """ ACU controller properties """
    model: Optional[str] = None


class ACU(BaseModel):
    """ ACU object in a data center
    """
    uid: Optional[str] = ""
    valid_id: Optional[str] = None # Only letters, digits, and underscores are allowed. 
    geometry: Optional[ACUGeometry] = Field(default_factory=ACUGeometry)
    cooling: Optional[ACUCooling] = Field(default_factory=ACUCooling)
    power: Optional[ACUPower] = Field(default_factory=ACUPower)
    controller: Optional[ACUController] = Field(default_factory=ACUController)
    model: Optional[Any] = None
    meta: Optional[OrderedDict] = Field(default_factory=dict)

    @property
    def k(self) -> float:
        """turbulent kinetic energy
        Others:
        omega = epsilon / (0.09 * k)
        """
        tu = 0.1
        u = float(self.cooling.operating.supply_air_volume_flow_rate / self.geometry.supply_area)
        k = 0.5 * (tu * u) ** 2
        return k

    @property
    def epsilon(self) -> float:
        """
        turbulent dissipation rate
        """
        nu = 1.5e-05
        eddy_viscosity_ratio = 10
        return 0.09 * (self.k ** 2) / (nu * eddy_viscosity_ratio)
