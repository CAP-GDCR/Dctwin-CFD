from typing import Optional
from pydantic import Field
from dclib.models.base import BaseModel
from dclib.models.geometry import Size


class CDUGeometryModel(BaseModel):
    """
    CDU geometry model. The CDU geometry is defined by the size of the CDU and the supply and return face of the CDU.
    """
    size: Optional[Size] = Field(default_factory=Size)
    main_pipe_diameter: Optional[float] = 0.01
