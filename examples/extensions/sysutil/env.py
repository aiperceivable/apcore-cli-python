"""system.env — Read environment variables."""

import os

from pydantic import BaseModel, Field


class Input(BaseModel):
    name: str = Field(..., description="Environment variable name to read")
    default: str = Field("", description="Value to return if the variable is not set")


class Output(BaseModel):
    name: str
    value: str
    exists: bool


class SystemEnv:
    """Read an environment variable value."""

    input_schema = Input
    output_schema = Output
    description = "Read an environment variable value"

    def execute(self, inputs, context=None):
        name = inputs["name"]
        default = inputs.get("default", "")
        value = os.environ.get(name, default)
        return {"name": name, "value": value, "exists": name in os.environ}
