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
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS stored_card_token VARCHAR(255)",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE",
    ]

    with engine.begin() as connection:
        if connection.dialect.name != "postgresql":
            return

        for statement in statements:
            connection.execute(text(statement))


def bootstrap_seed_data(db: Session) -> None:
    seed_magazines(db)
    seed_subscription_plans(db)
