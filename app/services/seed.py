"""Idempotent catalogue updates. Historical plans and customer records are preserved."""
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.magazine import Magazine, MagazineDocument
from app.models.subscription import SubscriptionPlan

DEFAULT_MAGAZINES = [
    {
        "slug": "special-issue-sh40", "title": "Longevity",
        "eyebrow": "2026 | Special Issue No. 40",
        "description": "Healthy ageing, prevention, nutrition and integrative longevity medicine. Complete English special edition, 116 pages.",
        "pdf_filename": "sh40-longevity-en.pdf", "volume_year": 2026,
        "issue_type": "special", "issue_number": "40", "language": "en",
        "cover_image": "/covers/sh40-longevity.jpg",
        "documents": [{"section": "complete", "pdf_filename": "sh40-longevity-en.pdf", "page_count": 116, "position": 0}],
    },
    {
        "slug": "main-issue-194", "title": "Collagen & Integrative Medicine",
        "eyebrow": "2026 | Main Issue No. 194",
        "description": "Collagen, gut health, mitochondrial medicine and orthomolecular care. The Basic section (32 pages) and Medical section (83 pages) are included together.",
        "pdf_filename": "194-basic-en.pdf", "volume_year": 2026,
        "issue_type": "classic", "issue_number": "194", "language": "en",
        "cover_image": "/covers/main-194.jpg?v=20260921",
        "documents": [
            {"section": "basic", "pdf_filename": "194-basic-en.pdf", "page_count": 32, "position": 0},
            {"section": "medical", "pdf_filename": "194-medical-en.pdf", "page_count": 83, "position": 1},
        ],
    },
]
DEFAULT_PLANS = [
    {"code": "classic-annual", "name": "Classic subscription", "category": "classic", "amount": Decimal("88.00"), "interval": "annual", "price_display": "EUR 88 / calendar year", "description": "Four main issues per calendar year. Each includes the Basic and Medical sections."},
    {"code": "special-annual", "name": "Special edition subscription", "category": "special", "amount": Decimal("88.00"), "interval": "annual", "price_display": "EUR 88 / calendar year", "description": "Four special issues per calendar year, each as one complete edition."},
    {"code": "combined-annual", "name": "Combined subscription", "category": "combined", "amount": Decimal("160.00"), "interval": "annual", "price_display": "EUR 160 / calendar year", "description": "All eight issues: four main issues with both sections, plus four special issues. Favourite."},
    {"code": "single-issue", "name": "Single issue", "category": "single", "amount": Decimal("24.00"), "interval": "once", "price_display": "EUR 24 / issue", "description": "One digital issue. Main issues include both the Basic and Medical sections. No renewal."},
]


def seed_magazines(db: Session) -> None:
    for entry in DEFAULT_MAGAZINES:
        payload = {key: value for key, value in entry.items() if key != "documents"}
        magazine = db.scalar(select(Magazine).where(Magazine.slug == payload["slug"]))
        if magazine is None:
            magazine = Magazine(**payload)
            db.add(magazine)
            db.flush()
        else:
            for key, value in payload.items():
                setattr(magazine, key, value)
        for document in entry["documents"]:
            item = next((doc for doc in magazine.documents if doc.section == document["section"]), None)
            if item is None:
                magazine.documents.append(MagazineDocument(**document))
            else:
                for key, value in document.items():
                    setattr(item, key, value)
    db.commit()


def seed_subscription_plans(db: Session) -> None:
    for payload in DEFAULT_PLANS:
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == payload["code"]))
        if plan is None:
            plan = SubscriptionPlan(**payload, is_available=True)
            db.add(plan)
        else:
            for key, value in payload.items():
                setattr(plan, key, value)
            plan.is_available = True
    db.commit()
