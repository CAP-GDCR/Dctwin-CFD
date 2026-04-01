from typing import Optional, OrderedDict

from dclib.models.base import BaseModel
from .components import Components


class Branch(BaseModel):
    """
    Branches are the basic components of a plant loop. They are used to install equipment (devices).
    Side is used to indicate the side of the branch. For example, for a chilled water loop, it will be constructed with
    three branches: 'inlet' side, 'outlet' side, and 'middle' pipe. The 'inlet' side and 'outlet' side are used to
    install pipes and pumps, while the 'middle' pipe is used to install chillers and pumps.

    :param components: the components installed on the branch
    :param side: the side of the branch
    """
    uid: Optional[str] = ""
    components: Optional[Components] = None
    side: Optional[str] = ""


class Loop(BaseModel):
    uid: Optional[str] = ""
    supply_branches: OrderedDict[str, Branch]
    demand_branches: OrderedDict[str, Branch]
