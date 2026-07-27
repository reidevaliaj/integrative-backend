import app.models  # noqa: F401
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import engine
from app.services.seed import seed_magazines, seed_subscription_plans


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    apply_schema_updates()


def apply_schema_updates() -> None:
    statements = [
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS provider VARCHAR(50)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS billing_interval VARCHAR(50)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS current_period_start TIMESTAMPTZ",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS canceled_at TIMESTAMPTZ",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS latest_order_code VARCHAR(255)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS latest_transaction_ref VARCHAR(255)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS provider_customer_ref VARCHAR(255)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS provider_subscription_ref VARCHAR(255)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS provider_mandate_ref VARCHAR(255)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS next_payment_at TIMESTAMPTZ",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS stored_card_token VARCHAR(255)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS mollie_customer_id VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS mollie_customer_mode VARCHAR(20)",
        "ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS payment_kind VARCHAR(50) NOT NULL DEFAULT 'initial'",
        "ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS provider_customer_ref VARCHAR(255)",
        "ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS provider_subscription_ref VARCHAR(255)",
        "ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS provider_mandate_ref VARCHAR(255)",
        "ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS event_key VARCHAR(255)",
        "CREATE INDEX IF NOT EXISTS ix_users_mollie_customer_id ON users (mollie_customer_id)",
        "CREATE INDEX IF NOT EXISTS ix_payment_orders_latest_transaction_ref ON payment_orders (latest_transaction_ref)",
        "CREATE INDEX IF NOT EXISTS ix_user_subscriptions_provider_subscription_ref ON user_subscriptions (provider_subscription_ref)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_payment_events_event_key ON payment_events (event_key) WHERE event_key IS NOT NULL",
    ]

    with engine.begin() as connection:
        if connection.dialect.name != "postgresql":
            return

        for statement in statements:
            connection.execute(text(statement))


def bootstrap_seed_data(db: Session) -> None:
    seed_magazines(db)
    seed_subscription_plans(db)
