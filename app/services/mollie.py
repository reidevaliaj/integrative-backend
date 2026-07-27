from typing import Any

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
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/customers/{customer_id}/payments",
            json={
                "amount": {"currency": currency, "value": amount},
                "description": description,
                "sequenceType": "first",
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
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/customers/{customer_id}/subscriptions",
            json={
                "amount": {"currency": currency, "value": amount},
                "interval": "1 month",
                "startDate": start_date,
                "description": "OM & Nutrition monthly subscription",
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


mollie_service = MollieService()
