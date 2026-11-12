from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlogWrite(BaseModel):
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=3, max_length=200)
    excerpt: str = Field(min_length=10, max_length=500)
    body: str = Field(min_length=30, max_length=60000)
    category: str = Field(default="Journal notes", min_length=2, max_length=80)
    author: str = Field(default="OM & Nutrition", min_length=2, max_length=120)
    cover: Literal["journal", "sports", "preview", "fermented-foods"] = "journal"
    status: Literal["draft", "published"] = "draft"
    version: int | None = Field(default=None, ge=1)

    @field_validator("title", "excerpt", "body", "category", "author", mode="before")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class BlogSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    slug: str
    title: str
    excerpt: str
    category: str
    author: str
    cover: str
    published_at: datetime | None
    updated_at: datetime


class BlogRead(BlogSummary):
    body: str
    status: str
    version: int


class RevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version: int
    created_at: datetime


class RestoreRequest(BaseModel):
    version: int = Field(ge=1)
