from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EnqueueRequest(BaseModel):
    url: str | None = None
    urls: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def clean_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("urls")
    @classmethod
    def clean_urls(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class ReplayRequest(BaseModel):
    domain: str | None = None
    domains: list[str] = Field(default_factory=list)
    queue: str | None = None
    limit_per_domain: int | None = Field(default=None, ge=1)

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("domains")
    @classmethod
    def clean_domains(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]
