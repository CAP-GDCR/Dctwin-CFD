"""
Object models can be re-used to construct the same object in the building.
"""

from pydantic import BaseModel as PydanticBaseModel


def to_camel(string: str) -> str:
    words = string.split('_')
    return words[0] + ''.join(word.capitalize() for word in words[1:])


class BaseModel(PydanticBaseModel):
    class Config:
        alias_generator = to_camel
        protected_namespaces = ()
        arbitrary_types_allowed = True
