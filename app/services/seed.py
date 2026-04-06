from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.magazine import Magazine
from app.models.subscription import SubscriptionPlan

DEFAULT_MAGAZINES = [
    {
        "slug": "current-main-issue",
        "title": "Current Main Issue",
        "eyebrow": "2026 | No. 194",
        "description": "A flagship issue focused on integrative medicine, orthomolecular science, and current clinical perspectives.",
        "pdf_filename": "current-main-issue.pdf",
    },
    {
        "slug": "sample-issue-request",
        "title": "Sample Issue Request",
        "eyebrow": "Digital and Print",
        "description": "A sample edition designed to help new readers understand the editorial approach and scientific depth of the journal.",
        "pdf_filename": "sample-issue-request.pdf",
    },
    {
        "slug": "current-special-issue",
        "title": "Current Special Issue",
        "eyebrow": "Special Issue SH41",
        "description": "A focused special issue for readers who want deeper insight into selected topics within integrative medicine.",
        "pdf_filename": "current-special-issue.pdf",
    },
]

DEFAULT_PLAN = {
    "code": "digital-annual",
    "name": "Annual Digital Access",
    "description": "Full access to all published magazines and the subscriber dashboard. This is currently a fake subscription flow.",
    "interval": "yearly",
    "price_display": "EUR 99 / year",
}


def seed_magazines(db: Session) -> None:
    existing_slugs = set(db.scalars(select(Magazine.slug)).all())
    created = False
    for payload in DEFAULT_MAGAZINES:
        if payload["slug"] in existing_slugs:
            continue
        db.add(Magazine(**payload))
        created = True

    if created:
        db.commit()


def seed_subscription_plans(db: Session) -> None:
    existing_plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == DEFAULT_PLAN["code"]))
    if existing_plan:
        return

    db.add(SubscriptionPlan(**DEFAULT_PLAN))
    db.commit()
