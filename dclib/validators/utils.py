from typing import Any, Optional
from loguru import logger


def validate_model_block(
    component: Any,
    model_type: str,
    model_definitions: Optional[dict],
    uid: str,
    required: bool = True,
) -> bool:
    """
    Validate a model block by checking its existence, name, and definitions.

    Args:
        component: The object (e.g., ACU, Server)
        model_type: The type of model block in constructions to validate ("cooling", "power", "geometry", controller, etc.)
        model_definitions: Dictionary of available models to validate against
        uid: The unique identifier of the component
        required: Whether the model block is required

    Raises:
        ValueError: If required and missing, or if model name is invalid, or model not found in definitions.
    """
    model_block = getattr(component, model_type, None)
    model_name = getattr(model_block, "model", None) if model_block else None

    if model_block is None or not model_name:
        if model_name == "":
            raise ValueError(f"Invalid {model_type} model name for {component.__class__.__name__} '{uid}'.")
        if required:
            raise ValueError(
                f"The object {model_type} model is not defined for {component.__class__.__name__} '{uid}'."
            )
        return False

    if not model_definitions:
        raise ValueError(
            f"{model_type.capitalize()} model definitions are missing when validating {component.__class__.__name__} '{uid}'."
        )

    if model_name not in model_definitions:
        raise ValueError(
            f"{model_type.capitalize()} model '{model_name}' not found for {component.__class__.__name__} '{uid}'."
        )

    logger.debug(f"Validating {model_type} model '{model_name}' for {component.__class__.__name__} '{uid}'")
    return True
