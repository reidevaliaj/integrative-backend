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
    is_fake: bool = True


class UserSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
    plan: SubscriptionPlanRead


class SubscribeRequest(BaseModel):
    plan_id: int
