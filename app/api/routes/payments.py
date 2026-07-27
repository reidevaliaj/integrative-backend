from decimal import Decimal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.payment import PaymentOrder
from app.models.subscription import SubscriptionPlan
from app.models.user import User
from app.schemas.payment import CheckoutCreateRequest, MollieCheckoutCreateResponse, PaymentOrderRead
from app.services.mollie import MollieAPIError, MollieConfigurationError, mollie_service
from app.services.mollie_payments import (
    PaymentConflictError,
    PaymentValidationError,
    create_checkout,
    format_amount,
    process_mollie_payment,
    synchronize_order,
)

router = APIRouter()


def serialize_order(order: PaymentOrder) -> PaymentOrderRead:
    return PaymentOrderRead(
        order_code=order.order_code,
        status=order.status,
        provider=order.provider,
        payment_kind=order.payment_kind,
        amount=format_amount(Decimal(order.amount)),
        currency=order.currency,
        billing_interval=order.billing_interval,
        period_start=order.period_start,
        period_end=order.period_end,
        latest_transaction_ref=order.latest_transaction_ref,
        paid_at=order.paid_at,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.post("/mollie/checkout", response_model=MollieCheckoutCreateResponse)
def create_mollie_checkout(
    payload: CheckoutCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MollieCheckoutCreateResponse:
    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.id == payload.plan_id))
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    try:
        checkout = create_checkout(
            db,
            user=current_user,
            plan=plan,
            locale=payload.locale,
        )
    except PaymentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (MollieConfigurationError, PaymentValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except MollieAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mollie checkout could not be started: {exc}",
        ) from exc

    return MollieCheckoutCreateResponse(
        checkout_url=checkout.checkout_url,
        order_code=checkout.order_code,
        mode=checkout.mode,
    )


@router.get("/orders/{order_code}", response_model=PaymentOrderRead)
def get_order_status(
    order_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentOrderRead:
    order = db.scalar(
        select(PaymentOrder).where(
            PaymentOrder.order_code == order_code,
            PaymentOrder.user_id == current_user.id,
        )
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found")

    if order.provider == "mollie" and order.latest_transaction_ref and order.status == "pending":
        try:
            order = synchronize_order(db, order)
        except (MollieAPIError, PaymentValidationError):
            db.rollback()
            order = db.scalar(
                select(PaymentOrder).where(
                    PaymentOrder.order_code == order_code,
                    PaymentOrder.user_id == current_user.id,
                )
            )
    return serialize_order(order)


@router.post("/mollie/webhook", response_class=PlainTextResponse)
async def mollie_webhook(request: Request, db: Session = Depends(get_db)) -> PlainTextResponse:
    body = await request.body()
    payment_id = parse_qs(body.decode("utf-8"), keep_blank_values=False).get("id", [None])[0]
    if not payment_id or not payment_id.startswith("tr_") or len(payment_id) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment reference")

    try:
        payment = mollie_service.get_payment(payment_id)
    except MollieAPIError as exc:
        if exc.status_code == 404:
            return PlainTextResponse("OK")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mollie payment status is temporarily unavailable",
        ) from exc
    except MollieConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    try:
        process_mollie_payment(db, payment)
    except (MollieAPIError, PaymentValidationError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mollie payment processing will be retried",
        ) from exc

    return PlainTextResponse("OK")
