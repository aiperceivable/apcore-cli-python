"""math.add — Add two numbers."""

from pydantic import BaseModel, Field


class Input(BaseModel):
    a: int = Field(..., description="First operand")
    b: int = Field(..., description="Second operand")


class Output(BaseModel):
    sum: int


class MathAdd:
    """Add two numbers and return the sum."""

    input_schema = Input
    output_schema = Output
    description = "Add two numbers and return the sum"

    def execute(self, inputs, context=None):
        return {"sum": inputs["a"] + inputs["b"]}
