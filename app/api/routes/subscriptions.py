from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.schemas.subscription import SubscriptionPlanRead, UserSubscriptionRead
from app.services.subscriptions import expire_subscription_if_needed

router = APIRouter()


def serialize_plan(plan: SubscriptionPlan) -> SubscriptionPlanRead:
    return SubscriptionPlanRead(
        id=plan.id,
        code=plan.code,
        name=plan.name,
        description=plan.description,
        interval=plan.interval,
        price_display=plan.price_display,
        checkout_provider=None,
        checkout_enabled=False,
    )


def serialize_subscription(subscription: UserSubscription) -> UserSubscriptionRead:
    return UserSubscriptionRead(
        id=subscription.id,
        status=subscription.status,
        notes=subscription.notes,
        provider=subscription.provider,
        billing_interval=subscription.billing_interval,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        auto_renew=subscription.auto_renew,
        cancel_at_period_end=subscription.cancel_at_period_end,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
        plan=serialize_plan(subscription.plan),
    )


@router.get("/plans", response_model=list[SubscriptionPlanRead])
def list_plans(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SubscriptionPlanRead]:
    plans = db.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.id)).all()
    return [serialize_plan(plan) for plan in plans]


@router.get("/me", response_model=UserSubscriptionRead | None)
def get_my_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSubscriptionRead | None:
    subscription = db.scalar(
        select(UserSubscription)
        .options(joinedload(UserSubscription.plan))
        .where(UserSubscription.user_id == current_user.id)
        .order_by(UserSubscription.id.desc())
    )
    if subscription is None:
        return None
    subscription = expire_subscription_if_needed(db, subscription)
    return serialize_subscription(subscription)
