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

    mypos_mode: str = "disabled"
    mypos_configuration_pack: str | None = None
    mypos_sid: str | None = None
    mypos_wallet_number: str | None = None
    mypos_key_index: int | None = None
    mypos_private_key: str | None = None
    mypos_public_certificate: str | None = None
    mypos_currency: str = "EUR"
    mypos_language: str = "EN"
    mypos_payment_method: str = "1"
    mypos_payment_parameters_required: int = 3
    mypos_monthly_amount: Decimal | None = None
    mypos_recurring_enabled: bool = False
    mypos_request_card_token: bool = False
    subscription_price_display: str = "Monthly digital access"

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def mypos_enabled(self) -> bool:
        return self.mypos_mode in {"sandbox", "live"}

    @property
    def mypos_checkout_url(self) -> str:
        if self.mypos_mode == "sandbox":
            return "https://www.mypos.com/vmp/checkout-test"
        return "https://www.mypos.com/vmp/checkout"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
