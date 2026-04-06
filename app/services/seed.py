from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.magazine import Magazine
from app.models.subscription import SubscriptionPlan

DEFAULT_MAGAZINES = [
    {
        "slug": "special-issue-sh40",
        "title": "Special Issue SH40",
        "eyebrow": "Protected Digital Edition",
        "description": "A full journal issue for subscribed readers, delivered through the protected digital reader for integrative and orthomolecular medicine.",
        "pdf_filename": "1.) SH40 Internet - komplett.pdf",
    },
]

PLACEHOLDER_MAGAZINE_SLUGS = {
    "current-main-issue",
    "sample-issue-request",
    "current-special-issue",
}

PLACEHOLDER_MAGAZINE_FILES = {
    "current-main-issue.pdf",
    "sample-issue-request.pdf",
    "current-special-issue.pdf",
}

DEFAULT_PLAN = {
    "code": "digital-annual",
    "name": "Annual Digital Access",
    "description": "Full access to all published magazines and the subscriber dashboard. This is currently a fake subscription flow.",
    "interval": "yearly",
    "price_display": "EUR 99 / year",
}


def seed_magazines(db: Session) -> None:
    magazines = db.scalars(select(Magazine).order_by(Magazine.id)).all()
    magazines_by_slug = {magazine.slug: magazine for magazine in magazines}
    desired_slugs = {payload["slug"] for payload in DEFAULT_MAGAZINES}
    changed = False

    for magazine in magazines:
        if magazine.slug in PLACEHOLDER_MAGAZINE_SLUGS or magazine.pdf_filename in PLACEHOLDER_MAGAZINE_FILES:
            if magazine.slug not in desired_slugs:
                db.delete(magazine)
                changed = True

    for payload in DEFAULT_MAGAZINES:
        magazine = magazines_by_slug.get(payload["slug"])
        if magazine is None:
            db.add(Magazine(**payload))
            changed = True
            continue

        for field, value in payload.items():
            if getattr(magazine, field) != value:
                setattr(magazine, field, value)
                changed = True

        if not magazine.is_published:
            magazine.is_published = True
            changed = True

    if changed:
        db.commit()


def seed_subscription_plans(db: Session) -> None:
    existing_plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == DEFAULT_PLAN["code"]))
    if existing_plan:
        return

    db.add(SubscriptionPlan(**DEFAULT_PLAN))
    db.commit()
