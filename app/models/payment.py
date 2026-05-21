from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("subscription_plans.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), default="mypos", index=True)
    order_code: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(10), default="EUR")
    billing_interval: Mapped[str] = mapped_column(String(50), default="monthly")
    description: Mapped[str] = mapped_column(Text)
    customer_email: Mapped[str] = mapped_column(String(255))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latest_transaction_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stored_card_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="payment_orders")
    plan = relationship("SubscriptionPlan")
    events = relationship("PaymentEvent", back_populates="order", cascade="all, delete-orphan")


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    payment_order_id: Mapped[int | None] = mapped_column(ForeignKey("payment_orders.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="mypos", index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    order = relationship("PaymentOrder", back_populates="events")
