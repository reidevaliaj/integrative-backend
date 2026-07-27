import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment import PaymentEvent, PaymentOrder
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.services.mollie import MollieAPIError, MollieService, mollie_service
from app.services.subscriptions import as_utc, compute_monthly_period, expire_subscription_if_needed, subscription_is_active, utc_now

TERMINAL_ORDER_STATUSES = {"paid", "canceled", "reversed", "failed"}
REMOTE_PENDING_STATUSES = {"open", "pending", "authorized"}
REMOTE_FAILED_STATUSES = {"failed", "expired"}


class PaymentConflictError(RuntimeError):
    pass


class PaymentValidationError(RuntimeError):
    pass


@dataclass
class CheckoutSession:
    checkout_url: str
    order_code: str
    mode: str


@dataclass
class PaymentProcessingResult:
    order: PaymentOrder | None
    duplicate: bool = False
    recognized: bool = True
    valid: bool = True


def format_amount(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def build_order_code(user_id: int) -> str:
    timestamp = utc_now().strftime("%Y%m%d%H%M%S")
    return f"OMN-{user_id}-{timestamp}-{secrets.token_hex(4).upper()}"


def mollie_locale(locale: str) -> str:
    return "de_DE" if locale.lower().startswith("de") else "en_GB"


def _checkout_link(payment: dict[str, Any]) -> str | None:
    link = payment.get("_links", {}).get("checkout", {}).get("href")
    return str(link) if link else None


def _metadata(resource: dict[str, Any]) -> dict[str, Any]:
    value = resource.get("metadata")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return as_utc(parsed)
    except ValueError:
        return None


def _parse_date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    except ValueError:
        return None


def _decimal_amount(resource: dict[str, Any]) -> Decimal | None:
    try:
        return Decimal(str(resource.get("amount", {}).get("value")))
    except (InvalidOperation, TypeError):
        return None


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    try:
        return int(metadata.get(key))
    except (TypeError, ValueError):
        return None


def _embedded_items(resource: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = resource.get("_embedded", {}).get(key, [])
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _is_fully_refunded(payment: dict[str, Any]) -> bool:
    paid_amount = _decimal_amount(payment)
    if paid_amount is None:
        return False

    refunded = Decimal("0")
    for refund in _embedded_items(payment, "refunds"):
        if refund.get("status") != "refunded":
            continue
        try:
            refunded += Decimal(str(refund.get("amount", {}).get("value")))
        except (InvalidOperation, TypeError):
            continue
    return refunded >= paid_amount


def _has_active_chargeback(payment: dict[str, Any]) -> bool:
    return any(not chargeback.get("reversedAt") for chargeback in _embedded_items(payment, "chargebacks"))


def _compact_payment_payload(payment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payment.get("id"),
        "mode": payment.get("mode"),
        "status": payment.get("status"),
        "sequenceType": payment.get("sequenceType"),
        "customerId": payment.get("customerId"),
        "subscriptionId": payment.get("subscriptionId"),
        "amount": payment.get("amount"),
        "metadata": _metadata(payment),
        "createdAt": payment.get("createdAt"),
        "paidAt": payment.get("paidAt"),
        "failedAt": payment.get("failedAt"),
        "canceledAt": payment.get("canceledAt"),
        "expiredAt": payment.get("expiredAt"),
        "refunds": [
            {
                "id": item.get("id"),
                "status": item.get("status"),
                "amount": item.get("amount"),
            }
            for item in _embedded_items(payment, "refunds")
        ],
        "chargebacks": [
            {
                "id": item.get("id"),
                "amount": item.get("amount"),
                "reversedAt": item.get("reversedAt"),
            }
            for item in _embedded_items(payment, "chargebacks")
        ],
    }


def _event_key(payment: dict[str, Any]) -> str:
    payload = json.dumps(_compact_payment_payload(payment), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"mollie:{payment.get('id')}:{digest}"


def _validate_payment(
    payment: dict[str, Any],
    order: PaymentOrder,
    *,
    mode: str,
) -> bool:
    amount = _decimal_amount(payment)
    currency = str(payment.get("amount", {}).get("currency") or "").upper()
    if payment.get("mode") != mode or amount != order.amount or currency != order.currency:
        return False

    customer_id = payment.get("customerId")
    if order.provider_customer_ref and customer_id != order.provider_customer_ref:
        return False

    metadata = _metadata(payment)
    if order.payment_kind == "initial":
        return (
            payment.get("sequenceType") == "first"
            and metadata.get("kind") == "initial"
            and metadata.get("order_code") == order.order_code
            and _metadata_int(metadata, "local_user_id") == order.user_id
            and _metadata_int(metadata, "local_plan_id") == order.plan_id
        )

    return (
        payment.get("sequenceType") == "recurring"
        and payment.get("subscriptionId") == order.provider_subscription_ref
    )


def _find_valid_mandate(mandates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for mandate in mandates:
        if mandate.get("status") == "valid":
            return mandate
    for mandate in mandates:
        if mandate.get("status") == "pending":
            return mandate
    return None


def _find_existing_remote_subscription(
    subscriptions: list[dict[str, Any]],
    initial_order_code: str,
) -> dict[str, Any] | None:
    for subscription in subscriptions:
        metadata = _metadata(subscription)
        if metadata.get("initial_order_code") == initial_order_code:
            return subscription
    return None


def _ensure_remote_subscription(
    db: Session,
    *,
    order: PaymentOrder,
    subscription: UserSubscription,
    mollie: MollieService,
) -> None:
    customer_id = order.provider_customer_ref
    if not customer_id:
        raise PaymentValidationError("Missing Mollie customer reference")

    remote_subscription: dict[str, Any] | None = None
    if subscription.provider_subscription_ref:
        try:
            candidate = mollie.get_subscription(customer_id, subscription.provider_subscription_ref)
        except MollieAPIError as exc:
            if exc.status_code != 404:
                raise
        else:
            if candidate.get("status") in {"active", "pending"}:
                remote_subscription = candidate

    if remote_subscription is None:
        remote_subscription = _find_existing_remote_subscription(
            mollie.list_customer_subscriptions(customer_id),
            order.order_code,
        )

    mandate = _find_valid_mandate(mollie.list_customer_mandates(customer_id))
    if mandate is None or not mandate.get("id"):
        raise MollieAPIError("The recurring payment mandate is not ready yet")

    if remote_subscription is None:
        start_date = (order.period_end + timedelta(seconds=1)).date().isoformat()
        remote_subscription = mollie.create_subscription(
            customer_id=customer_id,
            mandate_id=str(mandate["id"]),
            local_subscription_id=subscription.id,
            user_id=order.user_id,
            plan_id=order.plan_id,
            initial_order_code=order.order_code,
            start_date=start_date,
            amount=format_amount(order.amount),
            currency=order.currency,
        )

    remote_id = remote_subscription.get("id")
    if not remote_id or remote_subscription.get("status") not in {"active", "pending"}:
        raise MollieAPIError("Mollie did not activate the recurring subscription")

    subscription.provider_customer_ref = customer_id
    subscription.provider_subscription_ref = str(remote_id)
    subscription.provider_mandate_ref = str(mandate["id"])
    subscription.next_payment_at = _parse_date(remote_subscription.get("nextPaymentDate"))
    subscription.auto_renew = True
    subscription.cancel_at_period_end = False
    order.provider_subscription_ref = str(remote_id)
    order.provider_mandate_ref = str(mandate["id"])
    db.flush()


def _activate_initial_payment(
    db: Session,
    *,
    order: PaymentOrder,
    payment: dict[str, Any],
    mollie: MollieService,
) -> UserSubscription:
    paid_at = _parse_datetime(payment.get("paidAt")) or utc_now()
    order.period_start, order.period_end = compute_monthly_period(paid_at)
    order.paid_at = paid_at

    subscription = db.scalar(
        select(UserSubscription)
        .where(UserSubscription.user_id == order.user_id)
        .order_by(UserSubscription.id.desc())
        .with_for_update()
    )
    if subscription is None:
        subscription = UserSubscription(
            user_id=order.user_id,
            plan_id=order.plan_id,
            status="active",
        )
        db.add(subscription)
        db.flush()

    if subscription.latest_order_code != order.order_code:
        subscription.provider_subscription_ref = None
        subscription.provider_mandate_ref = None

    subscription.plan_id = order.plan_id
    subscription.status = "active"
    subscription.provider = "mollie"
    subscription.billing_interval = "monthly"
    subscription.current_period_start = order.period_start
    subscription.current_period_end = order.period_end
    subscription.activated_at = paid_at
    subscription.canceled_at = None
    subscription.latest_order_code = order.order_code
    subscription.latest_transaction_ref = str(payment["id"])
    subscription.provider_customer_ref = order.provider_customer_ref
    subscription.notes = "Monthly OM & Nutrition subscription payment confirmed."
    db.flush()

    _ensure_remote_subscription(
        db,
        order=order,
        subscription=subscription,
        mollie=mollie,
    )
    return subscription


def _activate_renewal_payment(
    *,
    order: PaymentOrder,
    payment: dict[str, Any],
    subscription: UserSubscription,
) -> None:
    paid_at = _parse_datetime(payment.get("paidAt")) or utc_now()
    period_start, period_end = compute_monthly_period(paid_at)
    order.period_start = period_start
    order.period_end = period_end
    order.paid_at = paid_at

    if subscription.current_period_end is None or period_end > as_utc(subscription.current_period_end):
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end

    subscription.status = "active"
    subscription.latest_order_code = order.order_code
    subscription.latest_transaction_ref = str(payment["id"])
    subscription.activated_at = paid_at
    subscription.auto_renew = True
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.next_payment_at = None
    subscription.notes = "Monthly OM & Nutrition renewal payment confirmed."


def _revoke_for_reversal(
    *,
    order: PaymentOrder,
    subscription: UserSubscription | None,
    mollie: MollieService,
) -> None:
    order.status = "reversed"
    order.reversed_at = utc_now()
    if subscription is None:
        return

    if subscription.provider_customer_ref and subscription.provider_subscription_ref:
        try:
            mollie.cancel_subscription(
                subscription.provider_customer_ref,
                subscription.provider_subscription_ref,
            )
        except MollieAPIError as exc:
            if exc.status_code != 404:
                raise

    subscription.status = "past_due"
    subscription.auto_renew = False
    subscription.cancel_at_period_end = False
    subscription.canceled_at = utc_now()
    subscription.next_payment_at = None
    subscription.notes = "Access paused after a reversed payment."


def _create_renewal_order(
    db: Session,
    *,
    payment: dict[str, Any],
    subscription: UserSubscription,
) -> PaymentOrder:
    created_at = _parse_datetime(payment.get("createdAt")) or utc_now()
    period_start, period_end = compute_monthly_period(created_at)
    amount = _decimal_amount(payment) or Decimal("0")
    order = PaymentOrder(
        user_id=subscription.user_id,
        plan_id=subscription.plan_id,
        provider="mollie",
        order_code=f"MOL-{payment['id']}",
        status="pending",
        amount=amount,
        currency=str(payment.get("amount", {}).get("currency") or "").upper(),
        billing_interval="monthly",
        payment_kind="renewal",
        description=str(payment.get("description") or "OM & Nutrition monthly renewal"),
        customer_email=subscription.user.email,
        period_start=period_start,
        period_end=period_end,
        latest_transaction_ref=str(payment["id"]),
        provider_customer_ref=subscription.provider_customer_ref,
        provider_subscription_ref=subscription.provider_subscription_ref,
        provider_mandate_ref=subscription.provider_mandate_ref,
    )
    db.add(order)
    db.flush()
    return order


def process_mollie_payment(
    db: Session,
    payment: dict[str, Any],
    *,
    mollie: MollieService = mollie_service,
) -> PaymentProcessingResult:
    payment_id = payment.get("id")
    if not isinstance(payment_id, str) or not payment_id.startswith("tr_"):
        raise PaymentValidationError("Invalid Mollie payment reference")

    event_key = _event_key(payment)
    if db.scalar(select(PaymentEvent.id).where(PaymentEvent.event_key == event_key)) is not None:
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.latest_transaction_ref == payment_id))
        return PaymentProcessingResult(order=order, duplicate=True)

    metadata = _metadata(payment)
    order = db.scalar(
        select(PaymentOrder)
        .where(PaymentOrder.latest_transaction_ref == payment_id)
        .order_by(PaymentOrder.id.desc())
        .with_for_update()
    )
    if order is None and metadata.get("order_code"):
        order = db.scalar(
            select(PaymentOrder)
            .where(PaymentOrder.order_code == str(metadata["order_code"]))
            .with_for_update()
        )

    subscription: UserSubscription | None = None
    remote_subscription_id = payment.get("subscriptionId")
    if remote_subscription_id:
        subscription = db.scalar(
            select(UserSubscription)
            .where(UserSubscription.provider_subscription_ref == str(remote_subscription_id))
            .order_by(UserSubscription.id.desc())
            .with_for_update()
        )

    if order is None and subscription is not None:
        order = _create_renewal_order(db, payment=payment, subscription=subscription)

    event = PaymentEvent(
        payment_order_id=order.id if order else None,
        provider="mollie",
        event_key=event_key,
        event_type=f"payment.{payment.get('status', 'unknown')}",
        signature_valid=False,
        payload=json.dumps(_compact_payment_payload(payment), sort_keys=True, ensure_ascii=True),
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.latest_transaction_ref == payment_id))
        return PaymentProcessingResult(order=order, duplicate=True)

    if order is None:
        db.commit()
        return PaymentProcessingResult(order=None, recognized=False, valid=False)

    if subscription is None and order.provider_subscription_ref:
        subscription = db.scalar(
            select(UserSubscription)
            .where(UserSubscription.provider_subscription_ref == order.provider_subscription_ref)
            .order_by(UserSubscription.id.desc())
            .with_for_update()
        )

    if not _validate_payment(payment, order, mode=mollie.mode):
        if order.status not in TERMINAL_ORDER_STATUSES:
            order.status = "failed"
            order.failed_at = utc_now()
        db.commit()
        return PaymentProcessingResult(order=order, valid=False)

    event.signature_valid = True
    order.signature_validated = True
    order.latest_transaction_ref = payment_id

    if _is_fully_refunded(payment) or _has_active_chargeback(payment):
        _revoke_for_reversal(order=order, subscription=subscription, mollie=mollie)
        db.commit()
        return PaymentProcessingResult(order=order)

    remote_status = str(payment.get("status") or "")
    if remote_status == "paid":
        order.status = "paid"
        order.failed_at = None
        order.canceled_at = None
        order.reversed_at = None
        if order.payment_kind == "initial":
            subscription = _activate_initial_payment(db, order=order, payment=payment, mollie=mollie)
        elif subscription is not None:
            _activate_renewal_payment(order=order, payment=payment, subscription=subscription)
    elif remote_status == "canceled":
        order.status = "canceled"
        order.canceled_at = _parse_datetime(payment.get("canceledAt")) or utc_now()
    elif remote_status in REMOTE_FAILED_STATUSES:
        order.status = "failed"
        order.failed_at = (
            _parse_datetime(payment.get("failedAt"))
            or _parse_datetime(payment.get("expiredAt"))
            or utc_now()
        )
    elif remote_status in REMOTE_PENDING_STATUSES:
        order.status = "pending"
    else:
        event.signature_valid = False
        db.commit()
        return PaymentProcessingResult(order=order, valid=False)

    db.commit()
    return PaymentProcessingResult(order=order)


def create_checkout(
    db: Session,
    *,
    user: User,
    plan: SubscriptionPlan,
    locale: str,
    mollie: MollieService = mollie_service,
) -> CheckoutSession:
    if not mollie.is_enabled:
        raise PaymentValidationError("Mollie checkout is not configured")

    subscription = db.scalar(
        select(UserSubscription)
        .where(UserSubscription.user_id == user.id)
        .order_by(UserSubscription.id.desc())
    )
    subscription = expire_subscription_if_needed(db, subscription)
    if subscription_is_active(subscription):
        raise PaymentConflictError("An active subscription already exists")

    recent_cutoff = utc_now() - timedelta(minutes=30)
    pending_order = db.scalar(
        select(PaymentOrder)
        .where(
            PaymentOrder.user_id == user.id,
            PaymentOrder.plan_id == plan.id,
            PaymentOrder.provider == "mollie",
            PaymentOrder.status == "pending",
            PaymentOrder.created_at >= recent_cutoff,
        )
        .order_by(PaymentOrder.id.desc())
    )
    if pending_order and pending_order.latest_transaction_ref:
        remote_payment = mollie.get_payment(pending_order.latest_transaction_ref)
        checkout_url = _checkout_link(remote_payment)
        if remote_payment.get("status") in REMOTE_PENDING_STATUSES and checkout_url:
            return CheckoutSession(
                checkout_url=checkout_url,
                order_code=pending_order.order_code,
                mode=mollie.mode,
            )
        process_mollie_payment(db, remote_payment, mollie=mollie)
        refreshed_subscription = db.scalar(
            select(UserSubscription)
            .where(UserSubscription.user_id == user.id)
            .order_by(UserSubscription.id.desc())
        )
        if subscription_is_active(refreshed_subscription):
            raise PaymentConflictError("An active subscription already exists")

    locale_code = mollie_locale(locale)
    if user.mollie_customer_id and user.mollie_customer_mode == mollie.mode:
        customer_id = user.mollie_customer_id
    else:
        customer = mollie.create_customer(
            name=user.full_name or user.email,
            email=user.email,
            user_id=user.id,
            locale=locale_code,
        )
        customer_id = str(customer.get("id") or "")
        if not customer_id.startswith("cst_"):
            raise PaymentValidationError("Mollie did not return a customer reference")
        user.mollie_customer_id = customer_id
        user.mollie_customer_mode = mollie.mode
        db.commit()

    period_start, period_end = compute_monthly_period()
    order = PaymentOrder(
        user_id=user.id,
        plan_id=plan.id,
        provider="mollie",
        order_code=build_order_code(user.id),
        status="pending",
        amount=mollie.amount,
        currency=mollie.currency,
        billing_interval="monthly",
        payment_kind="initial",
        description="OM & Nutrition monthly subscription",
        customer_email=user.email,
        period_start=period_start,
        period_end=period_end,
        provider_customer_ref=customer_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    frontend_url = settings.frontend_url.rstrip("/")
    redirect_url = f"{frontend_url}/dashboard/subscriptions?payment=return&order={order.order_code}"
    cancel_url = f"{frontend_url}/dashboard/subscriptions?payment=cancel&order={order.order_code}"
    try:
        payment = mollie.create_first_payment(
            customer_id=customer_id,
            order_code=order.order_code,
            user_id=user.id,
            plan_id=plan.id,
            description=f"OM & Nutrition monthly subscription - {order.order_code}",
            amount=format_amount(order.amount),
            currency=order.currency,
            locale=locale_code,
            redirect_url=redirect_url,
            cancel_url=cancel_url,
        )
    except Exception:
        order.status = "failed"
        order.failed_at = utc_now()
        db.commit()
        raise

    payment_id = str(payment.get("id") or "")
    checkout_url = _checkout_link(payment)
    if not payment_id.startswith("tr_") or not checkout_url:
        order.status = "failed"
        order.failed_at = utc_now()
        db.commit()
        raise PaymentValidationError("Mollie did not return a checkout session")

    order.latest_transaction_ref = payment_id
    if not _validate_payment(payment, order, mode=mollie.mode):
        order.status = "failed"
        order.failed_at = utc_now()
        db.commit()
        raise PaymentValidationError("Mollie checkout validation failed")

    db.commit()
    return CheckoutSession(
        checkout_url=checkout_url,
        order_code=order.order_code,
        mode=mollie.mode,
    )


def synchronize_order(
    db: Session,
    order: PaymentOrder,
    *,
    mollie: MollieService = mollie_service,
) -> PaymentOrder:
    if order.provider != "mollie" or not order.latest_transaction_ref:
        return order
    payment = mollie.get_payment(order.latest_transaction_ref)
    result = process_mollie_payment(db, payment, mollie=mollie)
    return result.order or order


def cancel_user_subscription(
    db: Session,
    *,
    user: User,
    mollie: MollieService = mollie_service,
) -> UserSubscription:
    subscription = db.scalar(
        select(UserSubscription)
        .where(UserSubscription.user_id == user.id)
        .order_by(UserSubscription.id.desc())
        .with_for_update()
    )
    subscription = expire_subscription_if_needed(db, subscription)
    if not subscription_is_active(subscription) or subscription is None:
        raise PaymentConflictError("No active subscription was found")
    if subscription.provider != "mollie":
        raise PaymentConflictError("This subscription cannot be canceled online")

    if subscription.provider_customer_ref and subscription.provider_subscription_ref:
        try:
            mollie.cancel_subscription(
                subscription.provider_customer_ref,
                subscription.provider_subscription_ref,
            )
        except MollieAPIError as exc:
            if exc.status_code != 404:
                raise

    subscription.auto_renew = False
    subscription.cancel_at_period_end = True
    subscription.canceled_at = utc_now()
    subscription.next_payment_at = None
    subscription.notes = "Monthly renewal canceled. Access remains available through the paid period."
    db.commit()
    db.refresh(subscription)
    return subscription
