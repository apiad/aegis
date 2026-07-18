from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class Meta(BaseModel):
    name: str
    description: str = ""


class SpawnTarget(BaseModel):
    kind: Literal["spawn"] = "spawn"
    profile: str


AnyTarget = Annotated[Union[SpawnTarget], Field(discriminator="kind")]


class AgentNode(BaseModel):
    type: Literal["agent"] = "agent"
    id: str | None = None
    prompt: str
    target: AnyTarget | None = None


class SequenceNode(BaseModel):
    type: Literal["sequence"] = "sequence"
    id: str | None = None
    children: list["AnyNode"]


AnyNode = Annotated[Union[SequenceNode, AgentNode], Field(discriminator="type")]


class Spec(BaseModel):
    meta: Meta
    args_schema: dict | None = None
    root: AnyNode


SequenceNode.model_rebuild()
