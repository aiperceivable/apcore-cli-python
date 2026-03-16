"""text.upper — Convert text to uppercase."""

from pydantic import BaseModel, Field


class Input(BaseModel):
    text: str = Field(..., description="Input string to convert")


class Output(BaseModel):
    result: str


class TextUpper:
    """Convert a string to uppercase."""

    input_schema = Input
    output_schema = Output
    description = "Convert a string to uppercase"

    def execute(self, inputs, context=None):
        return {"result": inputs["text"].upper()}
