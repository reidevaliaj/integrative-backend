from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.magazine import Magazine
from app.models.user import User
from app.schemas.magazine import MagazineRead

router = APIRouter()


def to_magazine_read(magazine: Magazine, request: Request) -> MagazineRead:
    pdf_url = str(request.base_url).rstrip("/") + f"/static/pdfs/{magazine.pdf_filename}"
    return MagazineRead(
        id=magazine.id,
        slug=magazine.slug,
        title=magazine.title,
        eyebrow=magazine.eyebrow,
        description=magazine.description,
        pdf_filename=magazine.pdf_filename,
        pdf_url=pdf_url,
        created_at=magazine.created_at,
    )


@router.get("/", response_model=list[MagazineRead])
def list_magazines(
    request: Request,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MagazineRead]:
    magazines = db.scalars(select(Magazine).where(Magazine.is_published.is_(True)).order_by(Magazine.id)).all()
    return [to_magazine_read(magazine, request) for magazine in magazines]


@router.get("/{slug}", response_model=MagazineRead)
def get_magazine(
    slug: str,
    request: Request,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MagazineRead:
    magazine = db.scalar(select(Magazine).where(Magazine.slug == slug, Magazine.is_published.is_(True)))
    if magazine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine not found")
    return to_magazine_read(magazine, request)
