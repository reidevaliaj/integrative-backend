from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CheckoutCreateRequest(BaseModel):
    plan_id: int
    locale: Literal["en", "de"] = "en"
    volume_year: int | None = Field(default=None, ge=1900, le=2200)
    magazine_id: int | None = Field(default=None, gt=0)
    terms_accepted: bool = False


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
    volume_year: int | None = None
    magazine_id: int | None = None
    mode: str | None = None
    period_start: datetime
    period_end: datetime
    latest_transaction_ref: str | None
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime
