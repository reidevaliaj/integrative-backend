from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MagazineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    eyebrow: str
    description: str
    pdf_filename: str
    pdf_url: str
    created_at: datetime
