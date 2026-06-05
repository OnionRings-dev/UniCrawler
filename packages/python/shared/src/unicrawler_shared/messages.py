from __future__ import annotations

from pydantic import BaseModel, Field


class VectorizeRequest(BaseModel):
    document_id: int = Field(gt=0)
    version_id: int = Field(gt=0)


class VectorizeRequestEnvelope(BaseModel):
    type: str
    version: int
    payload: VectorizeRequest
