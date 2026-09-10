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
from app.models.magazine import Magazine
from app.services.access import paid_orders, has_volume_overlap, magazine_is_accessible, categories
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.services.mollie import MollieAPIError, MollieService, mollie_service
from app.services.subscriptions import (as_utc, compute_monthly_period, expire_subscription_if_needed, subscription_is_active, utc_now, calendar_year, compute_annual_period, cancellation_effective_at, PUBLISHER_TIMEZONE)

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


def _validate_payment(payment: dict[str, Any], order: PaymentOrder, *, mode: str) -> bool:
    if (payment.get("mode") != mode or (order.provider_mode is not None and order.provider_mode != mode)
            or _decimal_amount(payment) != order.amount
            or str(payment.get("amount", {}).get("currency") or "").upper() != order.currency):
        return False
    if order.latest_transaction_ref and payment.get("id") != order.latest_transaction_ref:
        return False
    if order.provider_customer_ref and payment.get("customerId") != order.provider_customer_ref:
        return False
    metadata = _metadata(payment)
    if order.payment_kind == "initial":
        sequence = "first" if order.billing_interval in {"monthly", "annual"} else "oneoff"
        return (payment.get("sequenceType") == sequence and metadata.get("kind") == "initial"
                and metadata.get("order_code") == order.order_code
                and _metadata_int(metadata, "local_user_id") == order.user_id
                and _metadata_int(metadata, "local_plan_id") == order.plan_id)
    return (payment.get("sequenceType") == "recurring"
            and payment.get("subscriptionId") == order.provider_subscription_ref
            and metadata.get("kind") == "renewal"
            and _metadata_int(metadata, "local_user_id") == order.user_id
            and _metadata_int(metadata, "local_plan_id") == order.plan_id
            and _metadata_int(metadata, "local_subscription_id") == order.subscription_id)


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
        start_date = (as_utc(order.period_end) + timedelta(seconds=1)).astimezone(PUBLISHER_TIMEZONE).date().isoformat()
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
            interval="12 months" if order.billing_interval == "annual" else "1 month",
            description=f"OM & Nutrition {order.plan.name} - {subscription.id}",
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


def _activate_initial_payment(db: Session, *, order: PaymentOrder, payment: dict[str, Any], mollie: MollieService) -> UserSubscription | None:
    paid_at = _parse_datetime(payment.get("paidAt")) or utc_now()
    order.paid_at = paid_at
    if order.billing_interval not in {"annual", "monthly"}:
        return None  # One-off issue/archive access is recorded by the verified paid order.
    if order.billing_interval == "monthly":
        order.period_start, order.period_end = compute_monthly_period(paid_at)
    subscription = db.get(UserSubscription, order.subscription_id) if order.subscription_id else None
    if subscription is None:
        subscription = UserSubscription(user_id=order.user_id, plan_id=order.plan_id, status="active")
        db.add(subscription)
        db.flush()
        order.subscription_id = subscription.id
    subscription.status = "active"
    subscription.provider = "mollie"
    subscription.provider_mode = mollie.mode
    subscription.volume_year = order.volume_year
    subscription.billing_interval = order.billing_interval
    subscription.billing_amount = order.amount
    subscription.billing_currency = order.currency
    subscription.current_period_start = order.period_start
    subscription.current_period_end = order.period_end
    subscription.activated_at = paid_at
    subscription.latest_order_code = order.order_code
    subscription.latest_transaction_ref = str(payment["id"])
    subscription.provider_customer_ref = order.provider_customer_ref
    subscription.notes = "Payment confirmed. Paid volumes remain available in the library."
    db.flush()
    _ensure_remote_subscription(db, order=order, subscription=subscription, mollie=mollie)
    return subscription


def _activate_renewal_payment(*, order: PaymentOrder, payment: dict[str, Any], subscription: UserSubscription, mollie: MollieService) -> None:
    order.paid_at = _parse_datetime(payment.get("paidAt")) or utc_now()
    # The ordered volume is fixed at payment creation, even if settlement is delayed.
    if subscription.current_period_end is None or as_utc(order.period_end) >= as_utc(subscription.current_period_end):
        subscription.current_period_start = order.period_start
        subscription.current_period_end = order.period_end
        subscription.volume_year = order.volume_year
        subscription.status = "active"
        subscription.latest_order_code = order.order_code
        subscription.latest_transaction_ref = str(payment["id"])
        subscription.activated_at = order.paid_at
        subscription.next_payment_at = as_utc(order.period_end) + timedelta(seconds=1)
    if subscription.cancel_effective_at and as_utc(order.period_end) >= as_utc(subscription.cancel_effective_at):
        # The final owed renewal is paid; retain the cancellation request and stop billing.
        if subscription.provider_customer_ref and subscription.provider_subscription_ref:
            remote = mollie.get_subscription(subscription.provider_customer_ref, subscription.provider_subscription_ref)
            if remote.get("status") in {"active", "pending"}:
                mollie.cancel_subscription(subscription.provider_customer_ref, subscription.provider_subscription_ref)
        subscription.auto_renew = False
        subscription.next_payment_at = None
    subscription.notes = "Renewal payment confirmed. Paid volumes remain available in the library."


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


def _create_renewal_order(db: Session, *, payment: dict[str, Any], subscription: UserSubscription) -> PaymentOrder:
    created_at = _parse_datetime(payment.get("createdAt")) or utc_now()
    year = calendar_year(created_at) if subscription.billing_interval == "annual" else None
    period_start, period_end = compute_annual_period(year) if year else compute_monthly_period(created_at)
    # Validate against the agreed amount, never an amount taken from the callback.
    expected_amount = subscription.billing_amount
    if expected_amount is None:
        previous = db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == subscription.latest_order_code))
        expected_amount = previous.amount if previous else settings.mollie_monthly_amount
    order = PaymentOrder(
        user_id=subscription.user_id, plan_id=subscription.plan_id, provider="mollie",
        provider_mode=subscription.provider_mode, volume_year=year, subscription_id=subscription.id,
        order_code=f"MOL-{payment['id']}", status="pending", amount=expected_amount,
        currency=subscription.billing_currency or "EUR", billing_interval=subscription.billing_interval or "monthly",
        payment_kind="renewal", description=f"OM & Nutrition {year or 'monthly'} renewal",
        customer_email=subscription.user.email, period_start=period_start, period_end=period_end,
        latest_transaction_ref=str(payment["id"]), provider_customer_ref=subscription.provider_customer_ref,
        provider_subscription_ref=subscription.provider_subscription_ref, provider_mandate_ref=subscription.provider_mandate_ref,
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

    if subscription is None and order.subscription_id:
        subscription = db.get(UserSubscription, order.subscription_id)

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
        if order.status == "paid":
            db.commit()
            return PaymentProcessingResult(order=order, duplicate=True)
        order.status = "paid"
        order.failed_at = None
        order.canceled_at = None
        order.reversed_at = None
        if order.payment_kind == "initial":
            subscription = _activate_initial_payment(db, order=order, payment=payment, mollie=mollie)
        elif subscription is not None:
            _activate_renewal_payment(order=order, payment=payment, subscription=subscription, mollie=mollie)
    elif order.status in {"paid", "reversed"}:
        db.commit()
        return PaymentProcessingResult(order=order)
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


TERMS_VERSION = "calendar-2026-09-10"


def create_checkout(db: Session, *, user: User, plan: SubscriptionPlan, locale: str,
                    volume_year: int | None = None, magazine_id: int | None = None,
                    terms_accepted: bool = False, mollie: MollieService = mollie_service) -> CheckoutSession:
    if not mollie.is_enabled:
        raise PaymentValidationError("Mollie checkout is not configured")
    if not plan.is_available or plan.amount is None or plan.amount <= 0:
        raise PaymentConflictError("This plan is no longer offered. Choose a current annual plan.")
    if not terms_accepted:
        raise PaymentConflictError("Please acknowledge the displayed purchase and renewal conditions.")
    year = volume_year if volume_year is not None else calendar_year()
    if year > calendar_year() or year < 1900:
        raise PaymentConflictError("This volume is not available for purchase.")
    magazine = None
    if plan.category == "single":
        magazine = db.scalar(select(Magazine).where(Magazine.id == magazine_id, Magazine.is_published.is_(True)))
        if magazine is None or magazine.volume_year is None:
            raise PaymentConflictError("Select an available issue.")
        year = magazine.volume_year
    elif magazine_id is not None:
        raise PaymentConflictError("An annual plan cannot be attached to a single issue.")
    elif year != calendar_year() and db.scalar(select(Magazine.id).where(Magazine.volume_year == year, Magazine.is_published.is_(True)).limit(1)) is None:
        raise PaymentConflictError("This archive volume is not available yet.")
    interval = "single_issue" if magazine else "annual" if year == calendar_year() else "archive"
    period_start, period_end = compute_annual_period(year)

    # Serialise checkout preparation per customer. Pending orders prevent duplicate charges.
    user = db.scalar(select(User).where(User.id == user.id).with_for_update())
    orders = paid_orders(db, user.id, mode=mollie.mode)
    def check_ownership():
        if magazine and magazine_is_accessible(magazine, orders):
            raise PaymentConflictError("This issue is already available in your library.")
        if not magazine and has_volume_overlap(orders, year, plan.category):
            raise PaymentConflictError("You already own all or part of this volume. Choose the complementary subscription or open your library.")
        if not magazine and interval == "annual":
            subscriptions = db.scalars(select(UserSubscription).where(
                UserSubscription.user_id == user.id, UserSubscription.provider_mode == mollie.mode,
                UserSubscription.auto_renew.is_(True), UserSubscription.billing_interval == "annual",
            )).all()
            if any(categories(plan.category) & categories(sub.plan.category) for sub in subscriptions):
                raise PaymentConflictError("A renewing subscription already covers this issue type. Manage it in your account.")
    check_ownership()
    pending = db.scalars(select(PaymentOrder).where(
        PaymentOrder.user_id == user.id, PaymentOrder.provider == "mollie", PaymentOrder.provider_mode == mollie.mode,
        PaymentOrder.status == "pending", PaymentOrder.volume_year == year,
    ).order_by(PaymentOrder.id.desc())).all()
    for pending_order in pending:
        same = pending_order.plan_id == plan.id and pending_order.magazine_id == magazine_id
        overlap = (same or (pending_order.magazine_id is None and (magazine.issue_type in categories(pending_order.plan.category) if magazine else bool(categories(plan.category) & categories(pending_order.plan.category)))))
        if not overlap:
            continue
        if not pending_order.latest_transaction_ref:
            if as_utc(pending_order.created_at) > utc_now() - timedelta(minutes=5):
                raise PaymentConflictError("Your checkout is being prepared. Please try again shortly.")
            pending_order.status = "failed"
            continue
        remote_payment = mollie.get_payment(pending_order.latest_transaction_ref)
        checkout_url = _checkout_link(remote_payment)
        if remote_payment.get("status") in REMOTE_PENDING_STATUSES and checkout_url:
            if same:
                db.commit()
                return CheckoutSession(checkout_url, pending_order.order_code, mollie.mode)
            raise PaymentConflictError("Finish or cancel the existing checkout for this volume before choosing another plan.")
        process_mollie_payment(db, remote_payment, mollie=mollie)
        orders = paid_orders(db, user.id, mode=mollie.mode)
        check_ownership()
        user = db.scalar(select(User).where(User.id == user.id).with_for_update())

    locale_code = mollie_locale(locale)
    if user.mollie_customer_id and user.mollie_customer_mode == mollie.mode:
        customer_id = user.mollie_customer_id
    else:
        customer = mollie.create_customer(name=user.full_name or user.email, email=user.email, user_id=user.id, locale=locale_code)
        customer_id = str(customer.get("id") or "")
        if not customer_id.startswith("cst_"):
            raise PaymentValidationError("Mollie did not return a customer reference")
        user.mollie_customer_id = customer_id
        user.mollie_customer_mode = mollie.mode
    description = f"OM & Nutrition {year} - {magazine.title if magazine else plan.name}"
    order = PaymentOrder(
        user_id=user.id, plan_id=plan.id, provider="mollie", provider_mode=mollie.mode,
        order_code=build_order_code(user.id), status="pending", amount=plan.amount, currency="EUR",
        billing_interval=interval, payment_kind="initial", description=description,
        volume_year=year, magazine_id=magazine.id if magazine else None, terms_version=TERMS_VERSION,
        customer_email=user.email, period_start=period_start, period_end=period_end, provider_customer_ref=customer_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    frontend_url = settings.frontend_url.rstrip("/")
    redirect_url = f"{frontend_url}/dashboard/subscriptions?payment=return&order={order.order_code}"
    cancel_url = f"{frontend_url}/dashboard/subscriptions?payment=cancel&order={order.order_code}"
    try:
        payment = mollie.create_first_payment(
            customer_id=customer_id, order_code=order.order_code, user_id=user.id, plan_id=plan.id,
            description=f"{description} - {order.order_code}", amount=format_amount(order.amount), currency=order.currency,
            locale=locale_code, redirect_url=redirect_url, cancel_url=cancel_url, recurring=interval == "annual",
        )
    except Exception:
        order.status = "failed"
        order.failed_at = utc_now()
        db.commit()
        raise
    payment_id = str(payment.get("id") or "")
    checkout_url = _checkout_link(payment)
    order.latest_transaction_ref = payment_id
    if not payment_id.startswith("tr_") or not checkout_url or not _validate_payment(payment, order, mode=mollie.mode):
        order.status = "failed"
        order.failed_at = utc_now()
        db.commit()
        raise PaymentValidationError("Mollie checkout validation failed")
    db.commit()
    return CheckoutSession(checkout_url, order.order_code, mollie.mode)


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


def cancel_user_subscription(db: Session, *, user: User, subscription_id: int | None = None,
                             mollie: MollieService = mollie_service) -> UserSubscription:
    query = select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.provider == "mollie")
    if subscription_id is not None:
        query = query.where(UserSubscription.id == subscription_id)
    subscription = db.scalar(query.order_by(UserSubscription.id.desc()).with_for_update())
    if subscription is None or subscription.provider_mode not in {None, mollie.mode}:
        raise PaymentConflictError("No subscription was found in the current payment mode.")
    if subscription.cancel_at_period_end:
        return subscription
    if not subscription.auto_renew:
        raise PaymentConflictError("This subscription has no future renewals.")
    now = utc_now()
    effective = cancellation_effective_at(now) if subscription.billing_interval == "annual" else subscription.current_period_end
    final_payment_due = (subscription.billing_interval == "annual" and effective is not None
                         and calendar_year(effective) > calendar_year(now))
    if not subscription.provider_customer_ref or not subscription.provider_subscription_ref:
        raise PaymentConflictError("The recurring payment reference is not ready. Please try again shortly.")
    try:
        if final_payment_due:
            mollie.limit_subscription_to_final_payment(subscription.provider_customer_ref, subscription.provider_subscription_ref)
        else:
            mollie.cancel_subscription(subscription.provider_customer_ref, subscription.provider_subscription_ref)
    except MollieAPIError as exc:
        if exc.status_code != 404:
            raise
        if final_payment_due:
            raise PaymentConflictError("The final renewal could not be scheduled. Please contact the publisher.") from exc
    subscription.auto_renew = final_payment_due
    subscription.cancel_at_period_end = True
    subscription.cancel_effective_at = effective
    subscription.canceled_at = now
    if not final_payment_due:
        subscription.next_payment_at = None
    subscription.notes = "Cancellation recorded. Paid volumes remain in the library."
    db.commit()
    db.refresh(subscription)
    return subscription
