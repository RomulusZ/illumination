"""Shared base classes for Illumination contracts."""

from pydantic import BaseModel, ConfigDict


class IlluminationModel(BaseModel):
    """Base for immutable, closed Pydantic contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
