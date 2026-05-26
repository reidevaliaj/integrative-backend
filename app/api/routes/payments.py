import json
import secrets
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.payment import PaymentEvent, PaymentOrder
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.schemas.payment import CheckoutField, MyPosCheckoutCreateResponse, PaymentOrderRead
from app.schemas.subscription import SubscribeRequest
from app.services.mypos import MyPosConfigurationError, mypos_service
from app.services.subscriptions import compute_monthly_period, expire_subscription_if_needed, subscription_is_active, utc_now

router = APIRouter()


def _build_order_code(user_id: int) -> str:
    return f"OMN-{user_id}-{utc_now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4).upper()}"


def _payload_to_text(items: list[tuple[str, str]]) -> str:
    return json.dumps({key: value for key, value in items}, ensure_ascii=True)


async def _extract_ordered_items(request: Request) -> list[tuple[str, str]]:
    if request.method == "GET":
        return [(key, value) for key, value in request.query_params.multi_items()]

    body = await request.body()
    if not body:
        return []

    return [(key, value) for key, value in parse_qsl(body.decode("utf-8"), keep_blank_values=True)]


def _find_order(db: Session, order_code: str | None) -> PaymentOrder | None:
    if not order_code:
        return None
    return db.scalar(
        select(PaymentOrder)
        .options(joinedload(PaymentOrder.plan))
        .where(PaymentOrder.order_code == order_code)
    )


def _record_event(
    db: Session,
    *,
    order: PaymentOrder | None,
    event_type: str,
    ordered_items: list[tuple[str, str]],
    signature_valid: bool,
) -> None:
    db.add(
        PaymentEvent(
            payment_order_id=order.id if order else None,
            provider="mypos",
            event_type=event_type,
            signature_valid=signature_valid,
            payload=_payload_to_text(ordered_items),
        )
    )


def _serialize_order(order: PaymentOrder) -> PaymentOrderRead:
    return PaymentOrderRead(
        order_code=order.order_code,
        status=order.status,
        amount=f"{order.amount:.2f}",
        currency=order.currency,
        billing_interval=order.billing_interval,
        period_start=order.period_start,
        period_end=order.period_end,
        latest_transaction_ref=order.latest_transaction_ref,
        paid_at=order.paid_at,
        created_at=order.created_at,
    )


def _subscription_redirect_url(payment_state: str, order_code: str | None) -> str:
    suffix = f"&order={order_code}" if order_code else ""
    return f"{settings.frontend_url}/dashboard/subscriptions?payment={payment_state}{suffix}"


def _activate_subscription_for_paid_order(db: Session, *, order: PaymentOrder, card_token: str | None) -> None:
    subscription = db.scalar(
        select(UserSubscription)
        .where(UserSubscription.user_id == order.user_id)
        .order_by(UserSubscription.id.desc())
    )
    subscription = expire_subscription_if_needed(db, subscription)

    if subscription is None:
        subscription = UserSubscription(
            user_id=order.user_id,
            plan_id=order.plan_id,
            status="active",
        )
        db.add(subscription)

    subscription.plan_id = order.plan_id
    subscription.status = "active"
    subscription.provider = "mypos"
    subscription.billing_interval = order.billing_interval
    subscription.current_period_start = order.period_start
    subscription.current_period_end = order.period_end
    subscription.activated_at = order.paid_at or utc_now()
    subscription.canceled_at = None
    subscription.latest_order_code = order.order_code
    subscription.latest_transaction_ref = order.latest_transaction_ref
    subscription.notes = "Monthly myPOS payment confirmed."
    if card_token:
        subscription.stored_card_token = card_token


def _revoke_subscription_for_reversed_order(db: Session, order: PaymentOrder) -> None:
    subscription = db.scalar(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == order.user_id,
            UserSubscription.latest_order_code == order.order_code,
        )
        .order_by(UserSubscription.id.desc())
    )
    if subscription is None:
        return

    subscription.status = "past_due"
    subscription.notes = "Latest myPOS payment was reversed."
    subscription.canceled_at = utc_now()


@router.post("/mypos/checkout", response_model=MyPosCheckoutCreateResponse)
def create_mypos_checkout(
    payload: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MyPosCheckoutCreateResponse:
    try:
        amount = mypos_service.get_order_amount()
    except MyPosConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    if not mypos_service.is_checkout_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="myPOS checkout is not configured")

    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.id == payload.plan_id))
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    existing_subscription = db.scalar(
        select(UserSubscription)
        .where(UserSubscription.user_id == current_user.id)
        .order_by(UserSubscription.id.desc())
    )
    existing_subscription = expire_subscription_if_needed(db, existing_subscription)

    anchor = utc_now()
    if subscription_is_active(existing_subscription) and existing_subscription and existing_subscription.current_period_end:
        anchor = existing_subscription.current_period_end + timedelta(seconds=1)

    period_start, period_end = compute_monthly_period(anchor)
    order = PaymentOrder(
        user_id=current_user.id,
        plan_id=plan.id,
        provider="mypos",
        order_code=_build_order_code(current_user.id),
        status="pending",
        amount=amount,
        currency=mypos_service.currency,
        billing_interval=plan.interval,
        description=f"{plan.name} for OM & Nutrition",
        customer_email=current_user.email,
        period_start=period_start,
        period_end=period_end,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    try:
        fields = mypos_service.build_checkout_fields(order, current_user)
    except MyPosConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return MyPosCheckoutCreateResponse(
        checkout_url=mypos_service.checkout_url,
        mode=mypos_service.mode,
        order_code=order.order_code,
        fields=[CheckoutField(name=name, value=value) for name, value in fields],
    )


@router.get("/orders/{order_code}", response_model=PaymentOrderRead)
def get_order_status(
    order_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentOrderRead:
    order = db.scalar(
        select(PaymentOrder)
        .where(PaymentOrder.order_code == order_code, PaymentOrder.user_id == current_user.id)
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found")
    return _serialize_order(order)


@router.post("/mypos/notify")
async def mypos_notify(request: Request, db: Session = Depends(get_db)) -> PlainTextResponse:
    ordered_items = await _extract_ordered_items(request)
    payload = dict(ordered_items)
    event_type = payload.get("IPCmethod", "IPCPurchaseNotify")
    order = _find_order(db, payload.get("OrderID"))

    try:
        signature_valid = mypos_service.verify_payload(ordered_items)
    except MyPosConfigurationError:
        signature_valid = False

    _record_event(db, order=order, event_type=event_type, ordered_items=ordered_items, signature_valid=signature_valid)

    if not signature_valid:
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid myPOS signature")

    if order is None:
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found")

    if payload.get("Currency") != order.currency or Decimal(payload.get("Amount", "0")) != order.amount:
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment amount mismatch")

    if event_type == "IPCPurchaseRollback":
        order.status = "reversed"
        order.reversed_at = utc_now()
        order.latest_transaction_ref = payload.get("IPC_Trnref")
        _revoke_subscription_for_reversed_order(db, order)
        db.commit()
        return PlainTextResponse("OK")

    if order.status != "paid":
        card_token = payload.get("CardToken")
        order.status = "paid"
        order.signature_validated = True
        order.latest_transaction_ref = payload.get("IPC_Trnref")
        order.stored_card_token = card_token
        order.paid_at = utc_now()
        _activate_subscription_for_paid_order(db, order=order, card_token=card_token)
        db.commit()

    return PlainTextResponse("OK")


@router.api_route("/mypos/return/success", methods=["GET", "POST"])
async def mypos_return_success(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    ordered_items = await _extract_ordered_items(request)
    payload = dict(ordered_items)
    order = _find_order(db, payload.get("OrderID"))

    try:
        signature_valid = mypos_service.verify_payload(ordered_items) if ordered_items else False
    except MyPosConfigurationError:
        signature_valid = False

    if ordered_items:
        _record_event(
            db,
            order=order,
            event_type=payload.get("IPCmethod", "IPCPurchaseOK"),
            ordered_items=ordered_items,
            signature_valid=signature_valid,
        )
        db.commit()

    return RedirectResponse(
        url=_subscription_redirect_url("success", payload.get("OrderID")),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.api_route("/mypos/return/cancel", methods=["GET", "POST"])
async def mypos_return_cancel(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    ordered_items = await _extract_ordered_items(request)
    payload = dict(ordered_items)
    order = _find_order(db, payload.get("OrderID"))

    try:
        signature_valid = mypos_service.verify_payload(ordered_items) if ordered_items else False
    except MyPosConfigurationError:
        signature_valid = False

    if ordered_items:
        _record_event(
            db,
            order=order,
            event_type=payload.get("IPCmethod", "IPCPurchaseCancel"),
            ordered_items=ordered_items,
            signature_valid=signature_valid,
        )
        if order is not None and order.status == "pending":
            order.status = "canceled"
            order.canceled_at = utc_now()
        db.commit()

    return RedirectResponse(
        url=_subscription_redirect_url("cancel", payload.get("OrderID")),
        status_code=status.HTTP_303_SEE_OTHER,
    )
