from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Magazine(Base):
    __tablename__ = "magazines"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    eyebrow: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    pdf_filename: Mapped[str] = mapped_column(String(255))
    volume_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    issue_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    issue_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cover_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    documents = relationship("MagazineDocument", back_populates="magazine", cascade="all, delete-orphan", order_by="MagazineDocument.position")


class MagazineDocument(Base):
    __tablename__ = "magazine_documents"
    __table_args__ = (UniqueConstraint("magazine_id", "section", name="uq_magazine_document_section"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    magazine_id: Mapped[int] = mapped_column(ForeignKey("magazines.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(30))
    pdf_filename: Mapped[str] = mapped_column(String(255))
    page_count: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)
    magazine = relationship("Magazine", back_populates="documents")
