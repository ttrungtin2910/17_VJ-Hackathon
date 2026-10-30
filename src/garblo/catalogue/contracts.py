from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
UncertainField = Literal[
    "category",
    "color",
    "material",
    "season",
    "tags",
    "display_name",
    "subcategory",
    "pattern",
    "fit",
    "styles",
    "seasons",
]


class GarmentAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: ShortText | None
    color: ShortText | None
    material: ShortText | None
    season: ShortText | None
    tags: list[ShortText] = Field(max_length=20)


class ComponentDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    display_name: ShortText | None = None
    subcategory: ShortText | None = None
    pattern: ShortText | None = None
    fit: ShortText | None = None
    styles: list[ShortText] = Field(default_factory=list, max_length=20)
    seasons: list[ShortText] = Field(default_factory=list, max_length=20)
    uncertain_fields: list[UncertainField] = Field(default_factory=list, max_length=11)


class Component(GarmentAttributes):
    details: ComponentDetails = Field(default_factory=ComponentDetails)


class AnalysisResultV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[2]
    image_type: Literal["single_garment", "outfit", "unrelated", "unknown"]
    items: list[Component] = Field(max_length=20)

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> int:
        # Pydantic Literal[2] mặc định vẫn nhận 2.0, kể cả strict=True.
        if type(value) is not int or value != 2:
            raise ValueError("schema_version phải là số nguyên 2")
        return value
