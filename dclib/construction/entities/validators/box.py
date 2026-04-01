from typing import OrderedDict

from ..box import Box
from dclib.models.geometry.box import BoxGeometryModel


class BoxValidator:
    """ A class to validate boxes of a room """

    @classmethod
    def validate(
        cls,
        boxes: OrderedDict[str, Box],
        geometry_models: OrderedDict[str, BoxGeometryModel],
    ) -> None:
        for box_id, box in boxes.items():
            box.uid = box_id if box_id else box.uid
            cls._validate_geometry_models(box, geometry_models) if geometry_models else None

    @classmethod
    def _validate_geometry_models(
        cls,
        box: Box,
        models: OrderedDict[str, BoxGeometryModel],
    ) -> None:
        """
        Validate box geometry models
        :param box: box object
        :param models: box geometry models
        :return: None
        """
        model_name = box.geometry.model
        box.geometry.faces = models.get(model_name).faces
