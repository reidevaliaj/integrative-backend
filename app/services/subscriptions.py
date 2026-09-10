from calendar import monthrange
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.subscription import UserSubscription

PUBLISHER_TIMEZONE = ZoneInfo("Europe/Berlin")


def calendar_year(now: datetime | None = None) -> int:
    return as_utc(now or utc_now()).astimezone(PUBLISHER_TIMEZONE).year


def compute_annual_period(year: int) -> tuple[datetime, datetime]:
    start = datetime(year, 1, 1, tzinfo=PUBLISHER_TIMEZONE)
    end = datetime(year + 1, 1, 1, tzinfo=PUBLISHER_TIMEZONE) - timedelta(seconds=1)
    return as_utc(start), as_utc(end)


def cancellation_effective_at(now: datetime | None = None) -> datetime:
    local = as_utc(now or utc_now()).astimezone(PUBLISHER_TIMEZONE)
    year = local.year + (1 if local.month >= 11 else 0)
    return compute_annual_period(year)[1]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_one_calendar_month(value: datetime) -> datetime:
    value = as_utc(value)
    month = value.month + 1
    year = value.year
    if month > 12:
        month = 1
        year += 1

    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def compute_monthly_period(anchor: datetime | None = None) -> tuple[datetime, datetime]:
    period_start = as_utc(anchor) if anchor else utc_now()
    next_month = add_one_calendar_month(period_start)
    period_end = next_month - timedelta(seconds=1)
    return period_start, period_end


def subscription_is_active(subscription: UserSubscription | None, now: datetime | None = None) -> bool:
    if subscription is None or subscription.status != "active":
        return False

    if subscription.current_period_end is None:
        return True

    return as_utc(subscription.current_period_end) >= as_utc(now or utc_now())


def expire_subscription_if_needed(db: Session, subscription: UserSubscription | None) -> UserSubscription | None:
    if subscription is None:
        return None

    if (
        subscription.status == "active"
        and subscription.current_period_end is not None
        and as_utc(subscription.current_period_end) < utc_now()
    ):
        subscription.status = "expired"
        db.commit()
        db.refresh(subscription)

    return subscription
