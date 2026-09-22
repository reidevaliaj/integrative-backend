from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.schemas.subscription import SubscriptionPlanRead, UserSubscriptionRead, CancelSubscriptionRequest
from app.services.access import paid_orders, has_volume_overlap
from app.services.subscriptions import calendar_year, cancellation_effective_at
from app.models.magazine import Magazine
from app.services.mollie import MollieAPIError, mollie_service
from app.services.mollie_payments import PaymentConflictError, cancel_user_subscription, format_amount
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
        price_amount=format_amount(plan.amount if plan.amount is not None else settings.mollie_monthly_amount),
        price_currency=settings.mollie_currency.upper(),
        checkout_provider="mollie" if mollie_service.is_enabled else None,
        checkout_enabled=False,
        category=plan.category,
        mode=settings.mollie_mode,
    )


def serialize_subscription(subscription: UserSubscription) -> UserSubscriptionRead:
    return UserSubscriptionRead(
        id=subscription.id,
        status=subscription.status,
        notes=subscription.notes,
        provider=subscription.provider,
        billing_interval=subscription.billing_interval,
        volume_year=subscription.volume_year,
        provider_mode=subscription.provider_mode,
        cancel_effective_at=subscription.cancel_effective_at,
        cancellation_effective_if_requested=cancellation_effective_at() if subscription.billing_interval == "annual" else subscription.current_period_end,
        final_payment_due=subscription.auto_renew and subscription.cancel_at_period_end,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        auto_renew=subscription.auto_renew,
        cancel_at_period_end=subscription.cancel_at_period_end,
        next_payment_at=subscription.next_payment_at,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
        plan=serialize_plan(subscription.plan),
    )


@router.get("/plans", response_model=list[SubscriptionPlanRead])
def list_plans(
    volume_year: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SubscriptionPlanRead]:
    plans = db.scalars(select(SubscriptionPlan).where(SubscriptionPlan.is_available.is_(True)).order_by(SubscriptionPlan.id)).all()
    orders = paid_orders(db, current_user.id)
    result = []
    for plan in plans:
        item = serialize_plan(plan)
        item.checkout_enabled = plan.amount is not None and mollie_service.checkout_available(
            amount=format_amount(plan.amount),
            recurring=plan.category != "single" and (volume_year or calendar_year()) == calendar_year(),
        )
        item.owned = has_volume_overlap(orders, volume_year or calendar_year(), plan.category)
        result.append(item)
    return result


@router.get("/volumes", response_model=list[int])
def available_volumes(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[int]:
    years = db.scalars(select(Magazine.volume_year).where(Magazine.is_published.is_(True), Magazine.volume_year.is_not(None)).distinct()).all()
    return sorted(set(years) | {calendar_year()}, reverse=True)


@router.get("/all", response_model=list[UserSubscriptionRead])
def all_my_subscriptions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[UserSubscriptionRead]:
    subscriptions = db.scalars(select(UserSubscription).options(joinedload(UserSubscription.plan)).where(
        UserSubscription.user_id == current_user.id,
        UserSubscription.provider == "mollie", UserSubscription.provider_mode == settings.mollie_mode,
    ).order_by(UserSubscription.id.desc())).all()
    return [serialize_subscription(expire_subscription_if_needed(db, sub)) for sub in subscriptions]


@router.get("/me", response_model=UserSubscriptionRead | None)
def get_my_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSubscriptionRead | None:
    subscription = db.scalar(
        select(UserSubscription)
        .options(joinedload(UserSubscription.plan))
        .where(UserSubscription.user_id == current_user.id, UserSubscription.provider == "mollie", UserSubscription.provider_mode == settings.mollie_mode)
        .order_by(UserSubscription.id.desc())
    )
    if subscription is None:
        return None
    subscription = expire_subscription_if_needed(db, subscription)
    return serialize_subscription(subscription)


@router.post("/cancel", response_model=UserSubscriptionRead)
def cancel_my_subscription(
    payload: CancelSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSubscriptionRead:
    try:
        subscription = cancel_user_subscription(db, user=current_user, subscription_id=payload.subscription_id)
    except PaymentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MollieAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The subscription could not be canceled with Mollie. Please try again.",
        ) from exc

    subscription = db.scalar(
        select(UserSubscription)
        .options(joinedload(UserSubscription.plan))
        .where(UserSubscription.id == subscription.id)
    )
    return serialize_subscription(subscription)
