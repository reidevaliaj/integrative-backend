import resend

from app.core.config import settings
from app.models.user import User


def send_welcome_email(user: User) -> None:
    if not settings.resend_api_key or not settings.resend_from_email:
        return

    resend.api_key = settings.resend_api_key
    payload = {
        "from": settings.resend_from_email,
        "to": [user.email],
        "subject": "Welcome to OM & Nutrition",
        "html": f"""
            <h1>Welcome to OM & Nutrition</h1>
            <p>Hello {user.full_name or user.email},</p>
            <p>Your account is ready. You can now access your dashboard, magazines, and subscription area.</p>
            <p>We are glad to have you with us.</p>
        """,
    }
    if settings.resend_reply_to:
        payload["reply_to"] = settings.resend_reply_to
    resend.Emails.send(payload)
