"""Paid calendar volumes remain in the library, independently of future renewals."""
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.magazine import Magazine
from app.models.payment import PaymentOrder


def categories(category: str | None) -> set[str]:
    return {"classic", "special"} if category == "combined" else {category} if category in {"classic", "special"} else set()


def paid_orders(db: Session, user_id: int, *, mode: str | None = None) -> list[PaymentOrder]:
    return list(db.scalars(select(PaymentOrder).options(joinedload(PaymentOrder.plan)).where(
        PaymentOrder.user_id == user_id,
        PaymentOrder.provider == "mollie",
        PaymentOrder.provider_mode == (mode or settings.mollie_mode),
        PaymentOrder.status == "paid",
        PaymentOrder.signature_validated.is_(True),
        PaymentOrder.volume_year.is_not(None),
    )).all())


def order_covers_magazine(order: PaymentOrder, magazine: Magazine) -> bool:
    if order.volume_year != magazine.volume_year:
        return False
    if order.magazine_id is not None:
        return order.magazine_id == magazine.id
    return magazine.issue_type in categories(order.plan.category)


def magazine_is_accessible(magazine: Magazine, orders: list[PaymentOrder]) -> bool:
    return magazine.is_published and any(order_covers_magazine(order, magazine) for order in orders)


def has_volume_overlap(orders: list[PaymentOrder], year: int, category: str) -> bool:
    return any(order.volume_year == year and order.magazine_id is None and
               bool(categories(category) & categories(order.plan.category)) for order in orders)
