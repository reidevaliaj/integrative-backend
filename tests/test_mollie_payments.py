import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.base import Base
from app.models.payment import PaymentOrder
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.services.mollie import MollieAPIError
from app.services.mollie_payments import (
    cancel_user_subscription,
    create_checkout,
    process_mollie_payment,
)


class FakeMollieService:
    mode = "test"
    currency = "EUR"
    amount = Decimal("22.00")
    is_enabled = True

    def __init__(self) -> None:
        self.payments: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.subscription_create_count = 0
        self.canceled_subscription_ids: list[str] = []

    def create_customer(self, **_: Any) -> dict[str, Any]:
        return {"id": "cst_test_reader"}

    def create_first_payment(
        self,
        *,
        customer_id: str,
        order_code: str,
        user_id: int,
        plan_id: int,
        description: str,
        amount: str,
        currency: str,
        **_: Any,
    ) -> dict[str, Any]:
        payment_id = f"tr_{order_code}"
        payment = {
            "id": payment_id,
            "mode": "test",
            "status": "open",
            "sequenceType": "first",
            "customerId": customer_id,
            "amount": {"value": amount, "currency": currency},
            "description": description,
            "metadata": {
                "kind": "initial",
                "order_code": order_code,
                "local_user_id": user_id,
                "local_plan_id": plan_id,
            },
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "_links": {
                "checkout": {
                    "href": f"https://www.mollie.com/checkout/test/{payment_id}",
                }
            },
            "_embedded": {"refunds": [], "chargebacks": []},
        }
        self.payments[payment_id] = payment
        return payment

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return self.payments[payment_id]

    def list_customer_mandates(self, _: str) -> list[dict[str, Any]]:
        return [{"id": "mdt_test_reader", "status": "valid"}]

    def list_customer_subscriptions(self, _: str) -> list[dict[str, Any]]:
        return list(self.subscriptions.values())

    def create_subscription(
        self,
        *,
        customer_id: str,
        mandate_id: str,
        initial_order_code: str,
        start_date: str,
        amount: str,
        currency: str,
        local_subscription_id: int,
        user_id: int,
        plan_id: int,
    ) -> dict[str, Any]:
        self.subscription_create_count += 1
        subscription_id = f"sub_{initial_order_code}"
        subscription = {
            "id": subscription_id,
            "status": "active",
            "customerId": customer_id,
            "mandateId": mandate_id,
            "nextPaymentDate": start_date,
            "amount": {"value": amount, "currency": currency},
            "metadata": {
                "kind": "renewal",
                "local_subscription_id": local_subscription_id,
                "local_user_id": user_id,
                "local_plan_id": plan_id,
                "initial_order_code": initial_order_code,
            },
        }
        self.subscriptions[subscription_id] = subscription
        return subscription

    def get_subscription(self, _: str, subscription_id: str) -> dict[str, Any]:
        if subscription_id not in self.subscriptions:
            raise MollieAPIError("Not found", status_code=404)
        return self.subscriptions[subscription_id]

    def cancel_subscription(self, _: str, subscription_id: str) -> None:
        self.canceled_subscription_ids.append(subscription_id)
        if subscription_id in self.subscriptions:
            self.subscriptions[subscription_id]["status"] = "canceled"


class MolliePaymentFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.mollie = FakeMollieService()

        self.user = User(
            email="reader@example.com",
            full_name="Test Reader",
            hashed_password="not-used",
        )
        self.plan = SubscriptionPlan(
            code="monthly-digital-subscription",
            name="OM & Nutrition Monthly Subscription",
            description="Monthly access",
            interval="monthly",
            price_display="EUR 22 / month",
        )
        self.db.add_all([self.user, self.plan])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _create_paid_initial_subscription(self) -> tuple[PaymentOrder, UserSubscription, dict[str, Any]]:
        checkout = create_checkout(
            self.db,
            user=self.user,
            plan=self.plan,
            locale="en",
            mollie=self.mollie,
        )
        order = self.db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == checkout.order_code))
        payment = self.mollie.payments[order.latest_transaction_ref]
        payment["status"] = "paid"
        payment["paidAt"] = datetime.now(timezone.utc).isoformat()
        result = process_mollie_payment(self.db, payment, mollie=self.mollie)
        subscription = self.db.scalar(
            select(UserSubscription)
            .where(UserSubscription.user_id == self.user.id)
            .order_by(UserSubscription.id.desc())
        )
        return result.order, subscription, payment

    def test_initial_payment_activates_once(self) -> None:
        order, subscription, payment = self._create_paid_initial_subscription()

        self.assertEqual(order.status, "paid")
        self.assertEqual(subscription.status, "active")
        self.assertTrue(subscription.auto_renew)
        self.assertEqual(subscription.provider, "mollie")
        self.assertEqual(self.mollie.subscription_create_count, 1)

        duplicate = process_mollie_payment(self.db, payment, mollie=self.mollie)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(self.mollie.subscription_create_count, 1)

    def test_paid_renewal_extends_access(self) -> None:
        _, subscription, _ = self._create_paid_initial_subscription()
        previous_end = subscription.current_period_end
        paid_at = previous_end + timedelta(seconds=1)
        payment_id = "tr_monthly_renewal"
        renewal = {
            "id": payment_id,
            "mode": "test",
            "status": "paid",
            "sequenceType": "recurring",
            "customerId": subscription.provider_customer_ref,
            "subscriptionId": subscription.provider_subscription_ref,
            "amount": {"value": "22.00", "currency": "EUR"},
            "description": "OM & Nutrition monthly subscription",
            "metadata": self.mollie.subscriptions[subscription.provider_subscription_ref]["metadata"],
            "createdAt": paid_at.isoformat(),
            "paidAt": paid_at.isoformat(),
            "_embedded": {"refunds": [], "chargebacks": []},
        }
        self.mollie.payments[payment_id] = renewal

        result = process_mollie_payment(self.db, renewal, mollie=self.mollie)
        self.db.refresh(subscription)

        self.assertEqual(result.order.payment_kind, "renewal")
        self.assertEqual(result.order.status, "paid")
        self.assertGreater(subscription.current_period_end, previous_end)
        self.assertEqual(subscription.latest_transaction_ref, payment_id)

    def test_cancellation_stops_renewal_but_keeps_paid_access(self) -> None:
        _, subscription, _ = self._create_paid_initial_subscription()
        canceled = cancel_user_subscription(
            self.db,
            user=self.user,
            mollie=self.mollie,
        )

        self.assertEqual(canceled.status, "active")
        self.assertTrue(canceled.cancel_at_period_end)
        self.assertFalse(canceled.auto_renew)
        self.assertIn(subscription.provider_subscription_ref, self.mollie.canceled_subscription_ids)

    def test_chargeback_pauses_access(self) -> None:
        order, subscription, payment = self._create_paid_initial_subscription()
        payment["_embedded"]["chargebacks"] = [
            {
                "id": "chb_test",
                "amount": {"value": "22.00", "currency": "EUR"},
                "reversedAt": None,
            }
        ]

        result = process_mollie_payment(self.db, payment, mollie=self.mollie)
        self.db.refresh(subscription)

        self.assertEqual(result.order.id, order.id)
        self.assertEqual(result.order.status, "reversed")
        self.assertEqual(subscription.status, "past_due")
        self.assertFalse(subscription.auto_renew)


if __name__ == "__main__":
    unittest.main()
