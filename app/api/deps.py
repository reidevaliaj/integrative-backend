from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.subscription import UserSubscription
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        user_id = int(user_id)
    except Exception as exc:  # noqa: BLE001
        raise credentials_exception from exc

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def get_current_active_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSubscription | None:
    return db.scalar(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == current_user.id,
            UserSubscription.status == "active",
        )
        .order_by(UserSubscription.updated_at.desc(), UserSubscription.id.desc())
    )


def require_active_subscription(
    subscription: UserSubscription | None = Depends(get_current_active_subscription),
) -> UserSubscription:
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription required",
        )
    return subscription
