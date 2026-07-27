from datetime import datetime

from pydantic import BaseModel


class PaymentOrderRead(BaseModel):
    order_code: str
    status: str
    amount: str
    currency: str
    billing_interval: str
    period_start: datetime
    period_end: datetime
    latest_transaction_ref: str | None
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime
