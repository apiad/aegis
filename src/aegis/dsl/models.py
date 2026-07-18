from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Meta(BaseModel):
    name: str
    description: str = ""


class SpawnTarget(BaseModel):
    kind: Literal["spawn"] = "spawn"
    profile: str


AnyTarget = Annotated[Union[SpawnTarget], Field(discriminator="kind")]


class AgentNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal["agent"] = "agent"
    id: str | None = None
    prompt: str
    target: AnyTarget | None = None
    schema_: dict | None = Field(default=None, alias="schema")
    inputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("schema_")
    @classmethod
    def _valid_json_schema(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
        try:
            Draft202012Validator.check_schema(v)
        except SchemaError as e:
            raise ValueError(f"invalid JSON Schema: {e.message}") from e
        return v


class SequenceNode(BaseModel):
    type: Literal["sequence"] = "sequence"
    id: str | None = None
    children: list["AnyNode"]


class ParallelNode(BaseModel):
    type: Literal["parallel"] = "parallel"
    id: str | None = None
    children: list["AnyNode"]


class MapNode(BaseModel):
    type: Literal["map"] = "map"
    id: str
    over: str
    body: "AnyNode"
    concurrency: int | None = None


AnyNode = Annotated[
    Union[SequenceNode, ParallelNode, MapNode, AgentNode],
    Field(discriminator="type"),
]


class Spec(BaseModel):
    meta: Meta
    args_schema: dict | None = None
    root: AnyNode


SequenceNode.model_rebuild()
ParallelNode.model_rebuild()
MapNode.model_rebuild()
