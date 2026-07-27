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
    checkout_provider: str | None = None
    checkout_enabled: bool = False


class UserSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    notes: str | None
    provider: str | None
    billing_interval: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    auto_renew: bool
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime
    plan: SubscriptionPlanRead
