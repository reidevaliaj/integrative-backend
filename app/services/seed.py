from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.magazine import Magazine
from app.models.subscription import SubscriptionPlan

DEFAULT_MAGAZINES = [
    {
        "slug": "special-issue-sh40",
        "title": "Longevity",
        "eyebrow": "2026 | Special Issue No. 40",
        "description": "A digital special issue of OM & Nutrition dedicated to healthy aging, prevention, immune aging and integrative longevity medicine.",
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
    "code": "longevity-digital-issue",
    "name": "Digital Access - Longevity Issue",
    "description": "Online access to the Longevity special issue through the reader dashboard. This is currently a demo purchase flow.",
    "interval": "one-time",
    "price_display": "EUR 20 / issue",
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
    existing_plan = db.scalar(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.code.in_([DEFAULT_PLAN["code"], "digital-annual"]))
        .order_by(SubscriptionPlan.id)
    )
    if existing_plan is None:
        existing_plan = db.scalar(select(SubscriptionPlan).order_by(SubscriptionPlan.id))

    changed = False

    if existing_plan is None:
        db.add(SubscriptionPlan(**DEFAULT_PLAN))
        changed = True
    else:
        for field, value in DEFAULT_PLAN.items():
            if getattr(existing_plan, field) != value:
                setattr(existing_plan, field, value)
                changed = True

    if changed:
        db.commit()
