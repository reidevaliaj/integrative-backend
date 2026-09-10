from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MagazineDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    section: str
    page_count: int


class MagazineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    eyebrow: str
    description: str
    is_accessible: bool
    volume_year: int | None = None
    issue_type: str | None = None
    issue_number: str | None = None
    cover_image: str | None = None
    language: str = "en"
    documents: list[MagazineDocumentRead] = []
    created_at: datetime
