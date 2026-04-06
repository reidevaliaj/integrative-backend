import app.models  # noqa: F401
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import engine
from app.services.seed import seed_magazines, seed_subscription_plans


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def bootstrap_seed_data(db: Session) -> None:
    seed_magazines(db)
    seed_subscription_plans(db)
