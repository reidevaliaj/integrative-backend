from functools import lru_cache
from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Integrative Backend"
    environment: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60 * 24
    algorithm: str = "HS256"

    database_url: str = "postgresql+psycopg://integrative_user:integrative_password@localhost:5432/integrative_backend"
    base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000"

    resend_api_key: str | None = None
    resend_from_email: str | None = None
    resend_reply_to: str | None = None

    mollie_mode: str = "disabled"
    mollie_api_key: str | None = None
    mollie_api_url: str = "https://api.mollie.com/v2"
    mollie_monthly_amount: Decimal = Decimal("22.00")
    mollie_currency: str = "EUR"
    mollie_api_timeout_seconds: float = 15

    subscription_price_display: str = "EUR 22 / month"

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def mollie_enabled(self) -> bool:
        key = (self.mollie_api_key or "").strip()
        expected_prefix = {"test": "test_", "live": "live_"}.get(self.mollie_mode)
        return bool(expected_prefix and key.startswith(expected_prefix) and self.mollie_monthly_amount > 0)

    @property
    def mollie_webhook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.api_v1_prefix}/payments/mollie/webhook"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
