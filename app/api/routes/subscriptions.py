from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.schemas.subscription import SubscribeRequest, SubscriptionPlanRead, UserSubscriptionRead
from app.services.mypos import mypos_service
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
        is_fake=not mypos_service.is_checkout_enabled,
        checkout_provider="mypos" if mypos_service.is_checkout_enabled else None,
        checkout_enabled=mypos_service.is_checkout_enabled,
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


@router.post("/subscribe", response_model=UserSubscriptionRead)
def fake_subscribe(
    payload: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSubscriptionRead:
    if settings.mypos_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Demo subscription is disabled while myPOS checkout is enabled",
        )

    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.id == payload.plan_id))
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    subscription = db.scalar(
        select(UserSubscription)
        .options(joinedload(UserSubscription.plan))
        .where(UserSubscription.user_id == current_user.id)
        .order_by(UserSubscription.id.desc())
    )
    if subscription is None:
        subscription = UserSubscription(
            user_id=current_user.id,
            plan_id=plan.id,
            status="active",
            provider="demo",
            billing_interval=plan.interval,
            notes="Demo monthly subscription activated.",
        )
        db.add(subscription)
    else:
        subscription.plan_id = plan.id
        subscription.status = "active"
        subscription.provider = "demo"
        subscription.billing_interval = plan.interval
        subscription.notes = "Demo monthly subscription activated."

    db.commit()
    db.refresh(subscription)
    subscription = db.scalar(
        select(UserSubscription)
        .options(joinedload(UserSubscription.plan))
        .where(UserSubscription.id == subscription.id)
    )
    return serialize_subscription(subscription)
