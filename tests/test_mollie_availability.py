import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.mollie import MollieAPIError, MollieService


def methods(*ids):
    return {"_embedded": {"methods": [{"id": method} for method in ids]}}


class MollieAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            mollie_mode="live", mollie_currency="EUR", mollie_enabled=True,
            mollie_api_key="live_placeholder",
        )
        self.settings_patch = patch("app.services.mollie.settings", self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.clock = patch("app.services.mollie.monotonic", return_value=100)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.service = MollieService()
        self.service._request = Mock()

    def test_pending_review_stays_closed_then_approval_is_picked_up(self):
        self.service._request.side_effect = [methods(), methods("creditcard")]
        self.assertFalse(self.service.checkout_available(amount="24.00", recurring=False))
        self.now.return_value = 159
        self.assertFalse(self.service.checkout_available(amount="24.00", recurring=False))
        self.assertEqual(self.service._request.call_count, 1)
        self.now.return_value = 161
        self.assertTrue(self.service.checkout_available(amount="24.00", recurring=False))
        self.assertEqual(self.service._request.call_count, 2)

    def test_subscription_requires_first_payment_and_renewal_methods(self):
        self.service._request.side_effect = [methods("creditcard"), methods()]
        self.assertFalse(self.service.checkout_available(amount="160.00", recurring=True))
        self.assertEqual([call.kwargs["params"]["sequenceType"] for call in self.service._request.call_args_list],
                         ["first", "recurring"])
        self.now.return_value = 161
        self.service._request.side_effect = [methods("creditcard"), methods("creditcard")]
        self.assertTrue(self.service.checkout_available(amount="160.00", recurring=True))

    def test_outage_disables_checkout_without_breaking_catalogue(self):
        self.service._request.side_effect = MollieAPIError("Unavailable", status_code=503)
        self.assertFalse(self.service.checkout_available(amount="24.00", recurring=False))
        self.assertFalse(self.service.checkout_available(amount="24.00", recurring=False))
        self.assertEqual(self.service._request.call_count, 1)

    def test_amount_currency_and_credential_changes_do_not_reuse_stale_results(self):
        self.service._request.return_value = methods("creditcard")
        self.assertTrue(self.service.checkout_available(amount="24.00", recurring=False))
        self.service._request.return_value = methods()
        self.assertFalse(self.service.checkout_available(amount="160.00", recurring=False))
        self.settings.mollie_currency = "GBP"
        self.assertFalse(self.service.checkout_available(amount="24.00", recurring=False))
        self.settings.mollie_currency = "EUR"
        self.settings.mollie_api_key = "live_replacement_placeholder"
        self.assertFalse(self.service.checkout_available(amount="24.00", recurring=False))
        self.assertEqual(self.service._request.call_count, 4)

    def test_test_mode_does_not_require_live_approval_and_missing_config_stays_closed(self):
        self.settings.mollie_mode = "test"
        self.assertTrue(self.service.checkout_available(amount="160.00", recurring=True))
        self.settings.mollie_enabled = False
        self.assertFalse(self.service.checkout_available(amount="24.00", recurring=False))
        self.service._request.assert_not_called()
