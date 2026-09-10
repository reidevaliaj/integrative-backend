from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SubscriptionPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str
    interval: str
    price_display: str
    price_amount: str
    price_currency: str
    checkout_provider: str | None = None
    checkout_enabled: bool = False
    category: str | None = None
    mode: str = "disabled"
    owned: bool = False


class UserSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    notes: str | None
    provider: str | None
    billing_interval: str | None
    volume_year: int | None = None
    provider_mode: str | None = None
    cancel_effective_at: datetime | None = None
    cancellation_effective_if_requested: datetime | None = None
    final_payment_due: bool = False
    current_period_start: datetime | None
    current_period_end: datetime | None
    auto_renew: bool
    cancel_at_period_end: bool
    next_payment_at: datetime | None
    created_at: datetime
    updated_at: datetime
    plan: SubscriptionPlanRead


class CancelSubscriptionRequest(BaseModel):
    subscription_id: int | None = None
