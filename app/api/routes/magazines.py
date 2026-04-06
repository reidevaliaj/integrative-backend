from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_subscription, get_current_user, require_active_subscription
from app.db.session import get_db
from app.models.magazine import Magazine
from app.models.subscription import UserSubscription
from app.models.user import User
from app.schemas.magazine import MagazineRead

router = APIRouter()
PDF_DIRECTORY = Path(__file__).resolve().parents[2] / "static" / "pdfs"


def to_magazine_read(magazine: Magazine, is_accessible: bool) -> MagazineRead:
    return MagazineRead(
        id=magazine.id,
        slug=magazine.slug,
        title=magazine.title,
        eyebrow=magazine.eyebrow,
        description=magazine.description,
        is_accessible=is_accessible,
        created_at=magazine.created_at,
    )


@router.get("/", response_model=list[MagazineRead])
def list_magazines(
    _: User = Depends(get_current_user),
    active_subscription: UserSubscription | None = Depends(get_current_active_subscription),
    db: Session = Depends(get_db),
) -> list[MagazineRead]:
    magazines = db.scalars(select(Magazine).where(Magazine.is_published.is_(True)).order_by(Magazine.id)).all()
    is_accessible = active_subscription is not None
    return [to_magazine_read(magazine, is_accessible) for magazine in magazines]


@router.get("/{slug}", response_model=MagazineRead)
def get_magazine(
    slug: str,
    _: User = Depends(get_current_user),
    __: UserSubscription = Depends(require_active_subscription),
    db: Session = Depends(get_db),
) -> MagazineRead:
    magazine = db.scalar(select(Magazine).where(Magazine.slug == slug, Magazine.is_published.is_(True)))
    if magazine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine not found")
    return to_magazine_read(magazine, True)


@router.get("/{slug}/document")
def get_magazine_document(
    slug: str,
    _: User = Depends(get_current_user),
    __: UserSubscription = Depends(require_active_subscription),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    magazine = db.scalar(select(Magazine).where(Magazine.slug == slug, Magazine.is_published.is_(True)))
    if magazine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine not found")

    file_path = PDF_DIRECTORY / magazine.pdf_filename
    if not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine document not found")

    def iter_chunks():
        with file_path.open("rb") as file_handle:
            while chunk := file_handle.read(1024 * 1024):
                yield chunk

    headers = {
        "Cache-Control": "private, no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "Content-Disposition": f'inline; filename="{magazine.slug}.pdf"',
        "Content-Length": str(file_path.stat().st_size),
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "clipboard-read=(), clipboard-write=(), web-share=()",
        "Cross-Origin-Resource-Policy": "same-site",
        "Accept-Ranges": "none",
    }

    return StreamingResponse(iter_chunks(), media_type="application/pdf", headers=headers)
