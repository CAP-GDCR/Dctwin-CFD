from typing import Optional

from dclib.models.base import BaseModel


class Transformer(BaseModel):
    """
    TODO: Transformer is a device that converts alternating current from one voltage level to another.
    Please refer to the following link for more details:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-electric-load-center-generator.html#electricloadcentertransformer
    """
    uid: Optional[str] = ""
