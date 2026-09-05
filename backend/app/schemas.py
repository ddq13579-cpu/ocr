from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


FieldType = Literal["text", "number", "date", "boolean"]


class TemplateFieldInput(BaseModel):
    field_name: str = Field(min_length=1, max_length=120)
    field_key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=80)
    field_type: FieldType
    description: str = ""
    required: bool = False
    sort_order: int = 0


class TemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    fields: list[TemplateFieldInput] = []

    @field_validator("fields")
    @classmethod
    def unique_keys(cls, fields: list[TemplateFieldInput]):
        if len({field.field_key for field in fields}) != len(fields):
            raise ValueError("field_key must be unique within a template")
        return fields


class TemplateOutput(TemplateInput):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class RecordUpdate(BaseModel):
    json_data: dict[str, Any]


class LoginInput(BaseModel):
    username: str
    password: str
