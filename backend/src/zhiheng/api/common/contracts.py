from pydantic import BaseModel, ConfigDict


class EmptyRequest(BaseModel):
    """Actions accept an empty JSON object or the historical absent body."""

    model_config = ConfigDict(extra='forbid')
