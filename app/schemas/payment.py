from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CheckoutCreateRequest(BaseModel):
    plan_id: int
    locale: Literal["en", "de"] = "en"


class MollieCheckoutCreateResponse(BaseModel):
    checkout_url: str
    order_code: str
    mode: str


class PaymentOrderRead(BaseModel):
    order_code: str
    status: str
    provider: str
    payment_kind: str
    amount: str
    currency: str
    billing_interval: str
    period_start: datetime
    period_end: datetime
    latest_transaction_ref: str | None
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime
