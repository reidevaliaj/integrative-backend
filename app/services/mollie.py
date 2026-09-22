from typing import Any
from hashlib import sha256
from time import monotonic

import httpx

from app.core.config import settings


class MollieConfigurationError(RuntimeError):
    pass


class MollieAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MollieService:
    provider = "mollie"

    def __init__(self) -> None:
        self._methods_cache: dict[tuple[str, str, str, str, str], tuple[float, bool]] = {}

    @property
    def mode(self) -> str:
        return settings.mollie_mode

    @property
    def currency(self) -> str:
        return settings.mollie_currency.upper()

    @property
    def amount(self):
        return settings.mollie_monthly_amount

    @property
    def is_enabled(self) -> bool:
        return settings.mollie_enabled

    def _has_payment_method(self, *, amount: str, sequence: str) -> bool:
        # A valid live key does not mean Mollie has approved any payment methods.
        # Cache only briefly so approval takes effect without another deployment.
        credential = sha256((settings.mollie_api_key or "").encode()).hexdigest()
        cache_key = (credential, self.mode, self.currency, amount, sequence)
        now = monotonic()
        cached = self._methods_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]
        try:
            response = self._request("GET", "/methods", params={
                "sequenceType": sequence,
                "amount[value]": amount,
                "amount[currency]": self.currency,
            })
            available = bool(response.get("_embedded", {}).get("methods", []))
        except (MollieAPIError, MollieConfigurationError):
            available = False
        self._methods_cache = {key: value for key, value in self._methods_cache.items() if value[0] > now}
        self._methods_cache[cache_key] = (monotonic() + 60, available)
        return available

    def checkout_available(self, *, amount: str, recurring: bool) -> bool:
        if not self.is_enabled:
            return False
        if self.mode == "test":
            return True
        return self._has_payment_method(amount=amount, sequence="first" if recurring else "oneoff") and (
            not recurring or self._has_payment_method(amount=amount, sequence="recurring")
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled:
            raise MollieConfigurationError("Mollie checkout is not configured")

        headers = {
            "Authorization": f"Bearer {settings.mollie_api_key}",
            "Accept": "application/hal+json",
            "User-Agent": "OM-Nutrition/1.0",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = httpx.request(
                method,
                f"{settings.mollie_api_url.rstrip('/')}/{path.lstrip('/')}",
                headers=headers,
                json=json,
                params=params,
                timeout=settings.mollie_api_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise MollieAPIError("Mollie is temporarily unavailable") from exc

        if response.status_code == 204:
            return {}

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.is_error:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            message = str(detail or "Mollie rejected the request")
            raise MollieAPIError(message, status_code=response.status_code)

        if not isinstance(payload, dict):
            raise MollieAPIError("Mollie returned an invalid response")
        return payload

    def create_customer(
        self,
        *,
        name: str,
        email: str,
        user_id: int,
        locale: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/customers",
            json={
                "name": name,
                "email": email,
                "locale": locale,
                "metadata": {
                    "local_user_id": user_id,
                    "application": "om-nutrition",
                    "mode": self.mode,
                },
            },
            idempotency_key=f"omnutrition-customer-{self.mode}-{user_id}",
        )

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
        locale: str,
        redirect_url: str,
        cancel_url: str,
        recurring: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/customers/{customer_id}/payments",
            json={
                "amount": {"currency": currency, "value": amount},
                "description": description,
                "sequenceType": "first" if recurring else "oneoff",
                "redirectUrl": redirect_url,
                "cancelUrl": cancel_url,
                "webhookUrl": settings.mollie_webhook_url,
                "locale": locale,
                "metadata": {
                    "kind": "initial",
                    "order_code": order_code,
                    "local_user_id": user_id,
                    "local_plan_id": plan_id,
                },
            },
            idempotency_key=f"omnutrition-payment-{order_code}",
        )

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/payments/{payment_id}",
            params={"embed": "refunds,chargebacks"},
        )

    def list_customer_mandates(self, customer_id: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/customers/{customer_id}/mandates",
            params={"limit": 250},
        )
        return list(payload.get("_embedded", {}).get("mandates", []))

    def list_customer_subscriptions(self, customer_id: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/customers/{customer_id}/subscriptions",
            params={"limit": 250},
        )
        return list(payload.get("_embedded", {}).get("subscriptions", []))

    def create_subscription(
        self,
        *,
        customer_id: str,
        mandate_id: str,
        local_subscription_id: int,
        user_id: int,
        plan_id: int,
        initial_order_code: str,
        start_date: str,
        amount: str,
        currency: str,
        interval: str = "1 month",
        description: str = "OM & Nutrition subscription",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/customers/{customer_id}/subscriptions",
            json={
                "amount": {"currency": currency, "value": amount},
                "interval": interval,
                "startDate": start_date,
                "description": description,
                "mandateId": mandate_id,
                "webhookUrl": settings.mollie_webhook_url,
                "metadata": {
                    "kind": "renewal",
                    "local_subscription_id": local_subscription_id,
                    "local_user_id": user_id,
                    "local_plan_id": plan_id,
                    "initial_order_code": initial_order_code,
                },
            },
            idempotency_key=f"omnutrition-subscription-{initial_order_code}",
        )

    def get_subscription(self, customer_id: str, subscription_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/customers/{customer_id}/subscriptions/{subscription_id}",
        )

    def cancel_subscription(self, customer_id: str, subscription_id: str) -> None:
        self._request(
            "DELETE",
            f"/customers/{customer_id}/subscriptions/{subscription_id}",
        )

    def limit_subscription_to_final_payment(self, customer_id: str, subscription_id: str) -> None:
        remote = self.get_subscription(customer_id, subscription_id)
        # Mollie's `times` is the TOTAL scheduled payment count, not remaining payments.
        # Derive the already-consumed count from times/timesRemaining when bounded,
        # otherwise count all scheduled subscription payments, including failed ones.
        if remote.get("times") is not None and remote.get("timesRemaining") is not None:
            consumed = int(remote["times"]) - int(remote["timesRemaining"])
        else:
            consumed = 0
            from_id = None
            while True:
                params: dict[str, Any] = {"limit": 250}
                if from_id:
                    params["from"] = from_id
                page = self._request("GET", f"/customers/{customer_id}/subscriptions/{subscription_id}/payments", params=params)
                consumed += len(page.get("_embedded", {}).get("payments", []))
                next_link = page.get("_links", {}).get("next", {}) or {}
                href = next_link.get("href")
                if not href:
                    break
                from urllib.parse import parse_qs, urlparse
                next_id = parse_qs(urlparse(href).query).get("from", [None])[0]
                if not next_id or next_id == from_id:
                    raise MollieAPIError("Unable to determine the final renewal")
                from_id = next_id
        self._request("PATCH", f"/customers/{customer_id}/subscriptions/{subscription_id}", json={"times": consumed + 1})


mollie_service = MollieService()
