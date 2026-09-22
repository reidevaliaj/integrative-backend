import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from app.services.subscriptions import compute_annual_period, cancellation_effective_at, calendar_year, as_utc
from app.services.access import paid_orders, magazine_is_accessible
from app.services.seed import seed_magazines, seed_subscription_plans
from app.models.magazine import Magazine
from app.services.mollie_payments import PaymentConflictError

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
    checkout_ready = True

    def checkout_available(self, **_: Any) -> bool:
        return self.checkout_ready

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
        recurring: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        payment_id = f"tr_{order_code}"
        payment = {
            "id": payment_id,
            "mode": "test",
            "status": "open",
            "sequenceType": "first" if recurring else "oneoff",
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
        interval: str = "12 months",
        description: str = "Annual subscription",
    ) -> dict[str, Any]:
        self.subscription_create_count += 1
        subscription_id = f"sub_{initial_order_code}"
        subscription = {
            "id": subscription_id,
            "status": "active",
            "customerId": customer_id,
            "mandateId": mandate_id,
            "nextPaymentDate": start_date,
            "interval": interval,
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

    def limit_subscription_to_final_payment(self, _: str, subscription_id: str) -> None:
        self.subscriptions[subscription_id]["times"] = 1


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
        self.db.add(self.user)
        self.db.commit()
        seed_subscription_plans(self.db)
        seed_magazines(self.db)
        self.plan = self.db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "combined-annual"))
        self.classic = self.db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "classic-annual"))
        self.special = self.db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "special-annual"))
        self.single = self.db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "single-issue"))
        self.main = self.db.scalar(select(Magazine).where(Magazine.slug == "main-issue-194"))
        self.longevity = self.db.scalar(select(Magazine).where(Magazine.slug == "special-issue-sh40"))
        self.clock = patch("app.services.mollie_payments.utc_now", return_value=datetime(2026, 9, 10, tzinfo=timezone.utc))
        self.clock.start()
        self.year_clock = patch("app.services.subscriptions.utc_now", return_value=datetime(2026, 9, 10, tzinfo=timezone.utc))
        self.year_clock.start()
        self.mode_mock = patch("app.services.access.settings.mollie_mode", "test")
        self.mode_mock.start()

    def tearDown(self) -> None:
        self.clock.stop()
        self.year_clock.stop()
        self.mode_mock.stop()
        self.db.close()
        self.engine.dispose()

    def _create_paid_initial_subscription(self) -> tuple[PaymentOrder, UserSubscription, dict[str, Any]]:
        checkout = create_checkout(
            self.db,
            user=self.user,
            plan=self.plan,
            locale="en", terms_accepted=True,
            mollie=self.mollie,
        )
        order = self.db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == checkout.order_code))
        payment = self.mollie.payments[order.latest_transaction_ref]
        payment["status"] = "paid"
        payment["paidAt"] = "2026-09-10T12:00:00Z"
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

    def test_unavailable_methods_create_no_order_or_customer(self):
        from app.services.mollie_payments import PaymentValidationError
        self.mollie.checkout_ready = False
        with patch.object(self.mollie, "create_customer") as customer:
            with self.assertRaisesRegex(PaymentValidationError, "temporarily unavailable"):
                create_checkout(self.db, user=self.user, plan=self.plan, locale="en",
                                terms_accepted=True, mollie=self.mollie)
            customer.assert_not_called()
        self.assertEqual(list(self.db.scalars(select(PaymentOrder))), [])

    def test_catalogue_checks_annual_and_archive_payment_capabilities(self):
        from app.api.routes.subscriptions import list_plans
        with patch("app.api.routes.subscriptions.mollie_service.checkout_available",
                   side_effect=lambda *, amount, recurring: not recurring):
            current = list_plans(volume_year=2026, current_user=self.user, db=self.db)
            self.assertEqual({p.code: p.checkout_enabled for p in current}, {
                "classic-annual": False, "special-annual": False,
                "combined-annual": False, "single-issue": True,
            })
            self.main.volume_year = 2025
            self.db.commit()
            archive = list_plans(volume_year=2025, current_user=self.user, db=self.db)
            self.assertTrue(all(p.checkout_enabled for p in archive))

    def test_paid_renewal_extends_access(self) -> None:
        _, subscription, _ = self._create_paid_initial_subscription()
        previous_end = subscription.current_period_end
        paid_at = as_utc(previous_end) + timedelta(seconds=1)
        payment_id = "tr_annual_renewal"
        renewal = {
            "id": payment_id,
            "mode": "test",
            "status": "paid",
            "sequenceType": "recurring",
            "customerId": subscription.provider_customer_ref,
            "subscriptionId": subscription.provider_subscription_ref,
            "amount": {"value": "160.00", "currency": "EUR"},
            "description": "OM & Nutrition annual subscription",
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
        self.assertGreater(as_utc(subscription.current_period_end), as_utc(previous_end))
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
                "amount": {"value": "160.00", "currency": "EUR"},
                "reversedAt": None,
            }
        ]

        result = process_mollie_payment(self.db, payment, mollie=self.mollie)
        self.db.refresh(subscription)

        self.assertEqual(result.order.id, order.id)
        self.assertEqual(result.order.status, "reversed")
        self.assertEqual(subscription.status, "past_due")
        self.assertFalse(subscription.auto_renew)

    def buy(self, plan, magazine_id=None, year=2026):
        checkout = create_checkout(self.db, user=self.user, plan=plan, locale="en", terms_accepted=True,
                                   volume_year=year, magazine_id=magazine_id, mollie=self.mollie)
        order = self.db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == checkout.order_code))
        payment = self.mollie.payments[order.latest_transaction_ref]
        payment.update(status="paid", paidAt="2026-09-10T12:00:00Z")
        result = process_mollie_payment(self.db, payment, mollie=self.mollie)
        return result.order, payment

    def access(self, magazine):
        return magazine_is_accessible(magazine, paid_orders(self.db, self.user.id, mode="test"))

    def test_annual_prices_and_january_renewal(self):
        order, sub, _ = self._create_paid_initial_subscription()
        self.assertEqual(order.amount, Decimal("160.00"))
        self.assertEqual((as_utc(order.period_start), as_utc(order.period_end)), compute_annual_period(2026))
        remote = self.mollie.subscriptions[sub.provider_subscription_ref]
        self.assertEqual(remote["interval"], "12 months")
        self.assertEqual(remote["nextPaymentDate"], "2027-01-01")
        self.assertTrue(self.access(self.main))
        self.assertTrue(self.access(self.longevity))

    def test_classic_covers_both_sections_only(self):
        order, _ = self.buy(self.classic)
        self.assertEqual(order.amount, Decimal("88.00"))
        self.assertTrue(self.access(self.main))
        self.assertEqual([d.section for d in self.main.documents], ["basic", "medical"])
        self.assertFalse(self.access(self.longevity))

    def test_special_only_covers_special(self):
        self.buy(self.special)
        self.assertFalse(self.access(self.main))
        self.assertTrue(self.access(self.longevity))

    def test_complementary_subscriptions_coexist(self):
        self.buy(self.classic)
        self.buy(self.special)
        self.assertTrue(self.access(self.main))
        self.assertTrue(self.access(self.longevity))
        self.assertEqual(len(self.db.scalars(select(UserSubscription)).all()), 2)

    def test_single_main_issue_is_one_charge_for_both_sections(self):
        order, payment = self.buy(self.single, magazine_id=self.main.id)
        self.assertEqual(order.amount, Decimal("24.00"))
        self.assertEqual(payment["sequenceType"], "oneoff")
        self.assertEqual(self.mollie.subscription_create_count, 0)
        self.assertTrue(self.access(self.main))
        self.assertFalse(self.access(self.longevity))

    def test_archive_is_oneoff_and_keeps_its_year(self):
        self.main.volume_year = 2025
        self.db.commit()
        order, payment = self.buy(self.classic, year=2025)
        self.assertEqual(order.billing_interval, "archive")
        self.assertEqual(payment["sequenceType"], "oneoff")
        self.assertEqual(order.volume_year, 2025)
        self.assertTrue(self.access(self.main))
        self.assertFalse(self.access(self.longevity))
        self.assertEqual(self.mollie.subscription_create_count, 0)

    def test_future_issue_in_paid_volume_unlocks_automatically(self):
        self.buy(self.classic)
        issue = Magazine(slug="future-main", title="Next main issue", eyebrow="2026", description="New issue",
                         pdf_filename="later.pdf", volume_year=2026, issue_type="classic", is_published=True)
        self.db.add(issue)
        self.db.commit()
        self.assertTrue(self.access(issue))
        issue.volume_year = 2027
        self.assertFalse(self.access(issue))

    def test_cancellation_keeps_paid_volume_after_year_end(self):
        order, sub, _ = self._create_paid_initial_subscription()
        cancel_user_subscription(self.db, user=self.user, subscription_id=sub.id, mollie=self.mollie)
        sub.status = "expired"
        self.db.commit()
        self.assertTrue(self.access(self.main))
        self.assertTrue(self.access(self.longevity))

    def test_wrong_amount_mode_currency_and_metadata_never_unlock(self):
        for field, value in [("amount", {"value": "0.01", "currency": "EUR"}), ("mode", "live"),
                             ("amount", {"value": "160.00", "currency": "USD"}), ("metadata", {})]:
            with self.subTest(field=field, value=value):
                checkout = create_checkout(self.db, user=self.user, plan=self.plan, locale="en", terms_accepted=True, mollie=self.mollie)
                order = self.db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == checkout.order_code))
                payment = self.mollie.payments[order.latest_transaction_ref]
                payment.update(status="paid", paidAt="2026-09-10T12:00:00Z")
                payment[field] = value
                result = process_mollie_payment(self.db, payment, mollie=self.mollie)
                self.assertFalse(result.valid)
                self.assertFalse(self.access(self.main))

    def test_paid_test_orders_do_not_grant_live_access(self):
        self.buy(self.plan)
        self.assertEqual(paid_orders(self.db, self.user.id, mode="live"), [])

    def test_legacy_demo_rows_do_not_unlock_new_catalogue(self):
        sub = UserSubscription(user_id=self.user.id, plan_id=self.plan.id, status="active")
        self.db.add(sub)
        self.db.commit()
        self.assertFalse(self.access(self.main))
        self.buy(self.plan)
        self.assertTrue(self.access(self.main))

    def test_cannot_buy_owned_or_overlapping_volume(self):
        self.buy(self.classic)
        for plan, magazine in [(self.classic, None), (self.plan, None), (self.single, self.main.id)]:
            with self.assertRaises(PaymentConflictError):
                create_checkout(self.db, user=self.user, plan=plan, magazine_id=magazine, terms_accepted=True, locale="en", mollie=self.mollie)

    def test_checkout_is_reused(self):
        one = create_checkout(self.db, user=self.user, plan=self.plan, terms_accepted=True, locale="en", mollie=self.mollie)
        two = create_checkout(self.db, user=self.user, plan=self.plan, terms_accepted=True, locale="en", mollie=self.mollie)
        self.assertEqual(one.order_code, two.order_code)
        self.assertEqual(len(self.mollie.payments), 1)

    def test_removed_monthly_plan_is_not_sellable(self):
        old = SubscriptionPlan(code="monthly", name="Old", description="Old", interval="monthly", price_display="22")
        self.db.add(old)
        self.db.commit()
        with self.assertRaises(PaymentConflictError):
            create_checkout(self.db, user=self.user, plan=old, locale="en", terms_accepted=True, mollie=self.mollie)

    def test_conditions_are_required(self):
        with self.assertRaises(PaymentConflictError):
            create_checkout(self.db, user=self.user, plan=self.plan, locale="en", mollie=self.mollie)

    def test_future_volume_rejected(self):
        with self.assertRaises(PaymentConflictError):
            create_checkout(self.db, user=self.user, plan=self.plan, volume_year=2027, terms_accepted=True, locale="en", mollie=self.mollie)

    def test_october_31_deadline_uses_publisher_timezone(self):
        before = datetime(2026, 10, 31, 22, 59, 59, tzinfo=timezone.utc)
        after = before + timedelta(seconds=1)
        self.assertEqual(calendar_year(cancellation_effective_at(before)), 2026)
        self.assertEqual(calendar_year(cancellation_effective_at(after)), 2027)

    def test_late_cancellation_schedules_one_final_renewal(self):
        _, sub, _ = self._create_paid_initial_subscription()
        with patch("app.services.mollie_payments.utc_now", return_value=datetime(2026, 11, 1, tzinfo=timezone.utc)):
            canceled = cancel_user_subscription(self.db, user=self.user, subscription_id=sub.id, mollie=self.mollie)
        self.assertTrue(canceled.cancel_at_period_end)
        self.assertTrue(canceled.auto_renew)
        self.assertEqual(calendar_year(canceled.cancel_effective_at), 2027)
        self.assertEqual(self.mollie.subscriptions[sub.provider_subscription_ref]["times"], 1)
        self.assertTrue(self.access(self.main))

    def test_changed_paid_callback_does_not_undo_cancellation(self):
        _, sub, payment = self._create_paid_initial_subscription()
        cancel_user_subscription(self.db, user=self.user, subscription_id=sub.id, mollie=self.mollie)
        payment["_embedded"]["refunds"] = [{"id": "refund_pending", "status": "pending", "amount": {"value": "1.00", "currency": "EUR"}}]
        process_mollie_payment(self.db, payment, mollie=self.mollie)
        self.assertFalse(sub.auto_renew)
        self.assertEqual(self.mollie.subscription_create_count, 1)

    def test_refund_only_removes_the_reversed_purchase(self):
        _, payment = self.buy(self.classic)
        self.buy(self.special)
        payment["_embedded"]["refunds"] = [{"id": "refund_full", "status": "refunded", "amount": {"value": "88.00", "currency": "EUR"}}]
        process_mollie_payment(self.db, payment, mollie=self.mollie)
        self.assertFalse(self.access(self.main))
        self.assertTrue(self.access(self.longevity))

    def test_partial_refund_keeps_access(self):
        _, payment = self.buy(self.single, magazine_id=self.main.id)
        payment["_embedded"]["refunds"] = [{"id": "refund_partial", "status": "refunded", "amount": {"value": "2.00", "currency": "EUR"}}]
        process_mollie_payment(self.db, payment, mollie=self.mollie)
        self.assertTrue(self.access(self.main))

    def test_seed_is_idempotent(self):
        seed_magazines(self.db)
        seed_subscription_plans(self.db)
        self.assertEqual(len(self.db.scalars(select(Magazine)).all()), 2)
        self.assertEqual(len(self.main.documents), 2)
        self.assertEqual(len(self.db.scalars(select(SubscriptionPlan)).all()), 4)


    def renewal(self, sub, *, amount="160.00", created="2027-01-01T01:00:00Z", paid="2027-01-05T12:00:00Z"):
        return {"id": "tr_next_annual", "mode": "test", "status": "paid", "sequenceType": "recurring",
                "customerId": sub.provider_customer_ref, "subscriptionId": sub.provider_subscription_ref,
                "amount": {"value": amount, "currency": "EUR"},
                "metadata": self.mollie.subscriptions[sub.provider_subscription_ref]["metadata"],
                "createdAt": created, "paidAt": paid, "_embedded": {"refunds": [], "chargebacks": []}}

    def test_wrong_renewal_amount_never_grants_next_year(self):
        _, sub, _ = self._create_paid_initial_subscription()
        result = process_mollie_payment(self.db, self.renewal(sub, amount="0.01"), mollie=self.mollie)
        self.assertFalse(result.valid)
        self.main.volume_year = 2027
        self.assertFalse(self.access(self.main))

    def test_late_cancellation_final_payment_stops_future_billing(self):
        _, sub, _ = self._create_paid_initial_subscription()
        with patch("app.services.mollie_payments.utc_now", return_value=datetime(2026,11,2,tzinfo=timezone.utc)):
            cancel_user_subscription(self.db, user=self.user, subscription_id=sub.id, mollie=self.mollie)
        self.assertEqual(calendar_year(sub.current_period_end), 2026)
        result = process_mollie_payment(self.db, self.renewal(sub), mollie=self.mollie)
        self.assertEqual(result.order.volume_year, 2027)
        self.assertFalse(sub.auto_renew)
        self.assertTrue(sub.cancel_at_period_end)
        self.assertIsNotNone(sub.canceled_at)
        self.assertEqual(calendar_year(sub.current_period_end), 2027)
        self.main.volume_year = 2027
        self.assertTrue(self.access(self.main))

    def test_delayed_settlement_does_not_change_volume(self):
        _, sub, _ = self._create_paid_initial_subscription()
        result = process_mollie_payment(self.db, self.renewal(sub, paid="2028-01-02T01:00:00Z"), mollie=self.mollie)
        self.assertEqual(result.order.volume_year, 2027)
        self.assertEqual(calendar_year(result.order.period_end), 2027)

    def test_cannot_cancel_another_users_subscription(self):
        _, sub, _ = self._create_paid_initial_subscription()
        other = User(email="other@example.com", hashed_password="unused")
        self.db.add(other)
        self.db.commit()
        with self.assertRaises(PaymentConflictError):
            cancel_user_subscription(self.db, user=other, subscription_id=sub.id, mollie=self.mollie)
        self.assertTrue(sub.auto_renew)

    def test_document_routes_enforce_entitlement_for_each_section(self):
        from app.api.routes.magazines import get_magazine_document, get_magazine, list_magazines
        from fastapi import HTTPException
        from tempfile import TemporaryDirectory
        from pathlib import Path
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for filename in ["194-basic-en.pdf", "194-medical-en.pdf"]:
            (Path(directory.name) / filename).write_bytes(b"test document fixture")
        path_mock = patch("app.api.routes.magazines.PDF_DIRECTORY", Path(directory.name))
        path_mock.start()
        self.addCleanup(path_mock.stop)
        for section in ["basic", "medical"]:
            with self.assertRaises(HTTPException) as ctx:
                get_magazine_document(self.main.slug, section=section, current_user=self.user, db=self.db)
            self.assertEqual(ctx.exception.status_code, 403)
        self.buy(self.single, magazine_id=self.main.id)
        for section in ["basic", "medical"]:
            response = get_magazine_document(self.main.slug, section=section, current_user=self.user, db=self.db)
            self.assertEqual(response.media_type, "application/pdf")
            self.assertIn("private", response.headers["Cache-Control"])
        with self.assertRaises(HTTPException) as ctx:
            get_magazine(self.longevity.slug, current_user=self.user, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)
        results = list_magazines(current_user=self.user, db=self.db)
        self.assertEqual({item.slug for item in results if item.is_accessible}, {self.main.slug})



if __name__ == "__main__":
    unittest.main()
