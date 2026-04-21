from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.schemas.subscription import SubscribeRequest, SubscriptionPlanRead, UserSubscriptionRead

router = APIRouter()


@router.get("/plans", response_model=list[SubscriptionPlanRead])
def list_plans(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SubscriptionPlanRead]:
    plans = db.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.id)).all()
    return [SubscriptionPlanRead.model_validate(plan) for plan in plans]


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
    return UserSubscriptionRead.model_validate(subscription)


@router.post("/subscribe", response_model=UserSubscriptionRead)
def fake_subscribe(
    payload: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSubscriptionRead:
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
            notes="Demo access activated for the current digital issue.",
        )
        db.add(subscription)
    else:
        subscription.plan_id = plan.id
        subscription.status = "active"
        subscription.notes = "Demo access activated for the current digital issue."

    db.commit()
    db.refresh(subscription)
    subscription = db.scalar(
        select(UserSubscription)
        .options(joinedload(UserSubscription.plan))
        .where(UserSubscription.id == subscription.id)
    )
    return UserSubscriptionRead.model_validate(subscription)
