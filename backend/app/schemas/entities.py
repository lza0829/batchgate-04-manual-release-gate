from typing import Literal

from pydantic import BaseModel, Field


class MaxAttemptsBody(BaseModel):
    max_attempts: int = Field(ge=1, le=10)


class ReleaseGateBody(BaseModel):
    requires_release: bool


class ReleaseDecisionBody(BaseModel):
    job_id: int
    decision: Literal["released", "rejected"]
    actor: str = Field(default="operator", min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)
