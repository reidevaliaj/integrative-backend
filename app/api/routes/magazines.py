from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.services.access import paid_orders, magazine_is_accessible
from app.db.session import get_db
from app.models.magazine import Magazine
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
        volume_year=magazine.volume_year,
        issue_type=magazine.issue_type,
        issue_number=magazine.issue_number,
        cover_image=magazine.cover_image,
        language=magazine.language,
        documents=magazine.documents,
        created_at=magazine.created_at,
    )


@router.get("/", response_model=list[MagazineRead])
def list_magazines(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MagazineRead]:
    magazines = db.scalars(select(Magazine).options(selectinload(Magazine.documents)).where(Magazine.is_published.is_(True)).order_by(Magazine.volume_year.desc(), Magazine.id)).all()
    orders = paid_orders(db, current_user.id)
    return [to_magazine_read(magazine, magazine_is_accessible(magazine, orders)) for magazine in magazines]


@router.get("/{slug}", response_model=MagazineRead)
def get_magazine(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MagazineRead:
    magazine = db.scalar(select(Magazine).where(Magazine.slug == slug, Magazine.is_published.is_(True)))
    if magazine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine not found")
    if not magazine_is_accessible(magazine, paid_orders(db, current_user.id)):
        raise HTTPException(status_code=403, detail="Purchase this issue or its annual volume to read it")
    return to_magazine_read(magazine, True)


@router.get("/{slug}/document")
def get_magazine_document(
    slug: str,
    section: str | None = Query(default=None, max_length=30),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    magazine = db.scalar(select(Magazine).where(Magazine.slug == slug, Magazine.is_published.is_(True)))
    if magazine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine not found")

    if not magazine_is_accessible(magazine, paid_orders(db, current_user.id)):
        raise HTTPException(status_code=403, detail="Purchase this issue or its annual volume to read it")
    documents = magazine.documents
    document = next((item for item in documents if item.section == section), None) if section else (documents[0] if documents else None)
    if section and document is None:
        raise HTTPException(status_code=404, detail="Issue section not found")
    filename = document.pdf_filename if document else magazine.pdf_filename
    file_path = (PDF_DIRECTORY / filename).resolve()
    if not file_path.is_relative_to(PDF_DIRECTORY.resolve()):
        raise HTTPException(status_code=404, detail="Magazine document not found")
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
