import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.blog import BlogPost, BlogRevision
from app.models.user import User
from app.schemas.blog import BlogRead, BlogSummary, BlogWrite, RestoreRequest, RevisionRead

router = APIRouter()
FIELDS = ("slug", "title", "excerpt", "body", "category", "author", "cover", "status")


def require_editor(user: User = Depends(get_current_user)) -> User:
    if not user.is_editor:
        raise HTTPException(403, "Editor access required")
    return user


def remember(db: Session, post: BlogPost, editor_id: int | None) -> None:
    snapshot = {key: getattr(post, key) for key in FIELDS}
    snapshot["published_at"] = post.published_at.isoformat() if post.published_at else None
    db.add(BlogRevision(post_id=post.id, editor_id=editor_id, version=post.version, snapshot=json.dumps(snapshot)))


@router.get("/", response_model=list[BlogSummary])
def public_posts(response: Response, limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return list(db.scalars(select(BlogPost).where(
        BlogPost.status == "published", BlogPost.published_at <= datetime.now(timezone.utc),
    ).order_by(BlogPost.published_at.desc(), BlogPost.id.desc()).limit(limit)).all())


@router.get("/editor/posts", response_model=list[BlogRead])
def editor_posts(response: Response, _: User = Depends(require_editor), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    return list(db.scalars(select(BlogPost).order_by(BlogPost.updated_at.desc())).all())


@router.post("/editor/posts", response_model=BlogRead, status_code=201)
def create_post(payload: BlogWrite, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"version"})
    post = BlogPost(**data, published_at=datetime.now(timezone.utc) if payload.status == "published" else None)
    db.add(post)
    try:
        db.flush()
        remember(db, post, user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This URL is already in use. Choose another slug.")
    db.refresh(post)
    return post


@router.get("/editor/posts/{post_id}/revisions", response_model=list[RevisionRead])
def revisions(post_id: int, _: User = Depends(require_editor), db: Session = Depends(get_db)):
    if db.get(BlogPost, post_id) is None:
        raise HTTPException(404, "Post not found")
    return list(db.scalars(select(BlogRevision).where(BlogRevision.post_id == post_id).order_by(BlogRevision.version.desc())).all())


def save_post(db: Session, post_id: int, payload: BlogWrite, editor_id: int) -> BlogPost:
    post = db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(404, "Post not found")
    if payload.slug != post.slug:
        raise HTTPException(409, "The URL of an existing post cannot be changed.")
    if payload.version is None:
        raise HTTPException(409, "Reload this post before saving.")
    data = payload.model_dump(exclude={"version"})
    now = datetime.now(timezone.utc)
    data.update(version=payload.version + 1, updated_at=now,
                published_at=post.published_at or (now if payload.status == "published" else None))
    result = db.execute(update(BlogPost).where(BlogPost.id == post_id, BlogPost.version == payload.version).values(**data))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "A newer version exists. Reload before saving to avoid overwriting it.")
    db.flush()
    db.refresh(post)
    remember(db, post, editor_id)
    db.commit()
    db.refresh(post)
    return post


@router.post("/editor/posts/{post_id}", response_model=BlogRead)
def edit_post(post_id: int, payload: BlogWrite, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    return save_post(db, post_id, payload, user.id)


@router.post("/editor/posts/{post_id}/restore/{revision_id}", response_model=BlogRead)
def restore_post(post_id: int, revision_id: int, payload: RestoreRequest, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    revision = db.get(BlogRevision, revision_id)
    if revision is None or revision.post_id != post_id:
        raise HTTPException(404, "Revision not found")
    data = json.loads(revision.snapshot)
    data.pop("published_at", None)
    # Restores are drafts so an old version never becomes public accidentally.
    data.update(status="draft", version=payload.version)
    return save_post(db, post_id, BlogWrite(**data), user.id)


@router.get("/{slug}", response_model=BlogRead)
def public_post(slug: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    post = db.scalar(select(BlogPost).where(BlogPost.slug == slug, BlogPost.status == "published", BlogPost.published_at <= datetime.now(timezone.utc)))
    if post is None:
        raise HTTPException(404, "Post not found")
    return post
