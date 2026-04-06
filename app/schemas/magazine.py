from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MagazineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    eyebrow: str
    description: str
    is_accessible: bool
    created_at: datetime
