import base64
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import cached_property
from typing import Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import settings
from app.models.payment import PaymentOrder
from app.models.user import User


def _normalize_pem(value: str | None) -> str | None:
    if not value:
        return None

    normalized = value.strip()
    if "\\n" in normalized:
        normalized = normalized.replace("\\n", "\n")
    return normalized


@dataclass
class MyPosCredentials:
    sid: str
    wallet_number: str
    key_index: int
    private_key: str
    public_certificate: str


class MyPosConfigurationError(RuntimeError):
    pass


class MyPosService:
    def __init__(self) -> None:
        self.mode = settings.mypos_mode
        self.currency = settings.mypos_currency.upper()
        self.language = settings.mypos_language.upper()
        self.ipc_version = "1.4"
        self.payment_method = settings.mypos_payment_method
        self.payment_parameters_required = settings.mypos_payment_parameters_required
        self.request_card_token = settings.mypos_request_card_token

    @cached_property
    def credentials(self) -> MyPosCredentials:
        payload: dict[str, object] = {}
        if settings.mypos_configuration_pack:
            try:
                decoded = base64.b64decode(settings.mypos_configuration_pack).decode("utf-8")
                payload = json.loads(decoded)
            except Exception as exc:  # noqa: BLE001
                raise MyPosConfigurationError("Invalid myPOS configuration pack") from exc

        sid = str(payload.get("sid") or settings.mypos_sid or "").strip()
        wallet_number = str(payload.get("cn") or settings.mypos_wallet_number or "").strip()
        key_index_raw = payload.get("idx") if payload.get("idx") is not None else settings.mypos_key_index
        private_key = _normalize_pem(str(payload.get("pk") or settings.mypos_private_key or "").strip())
        public_certificate = _normalize_pem(str(payload.get("pc") or settings.mypos_public_certificate or "").strip())

        if not sid or not wallet_number or key_index_raw in (None, "") or not private_key or not public_certificate:
            raise MyPosConfigurationError("myPOS credentials are incomplete")

        return MyPosCredentials(
            sid=sid,
            wallet_number=wallet_number,
            key_index=int(key_index_raw),
            private_key=private_key,
            public_certificate=public_certificate,
        )

    @property
    def checkout_url(self) -> str:
        return settings.mypos_checkout_url

    @property
    def is_checkout_enabled(self) -> bool:
        if not settings.mypos_enabled:
            return False

        try:
            _ = self.credentials
            return settings.mypos_monthly_amount is not None and settings.mypos_monthly_amount > 0
        except MyPosConfigurationError:
            return False

    @property
    def should_request_card_token(self) -> bool:
        return settings.mypos_recurring_enabled and self.request_card_token

    def build_checkout_fields(self, order: PaymentOrder, user: User) -> list[tuple[str, str]]:
        if not self.is_checkout_enabled:
            raise MyPosConfigurationError("myPOS checkout is not configured")

        amount = self._format_amount(order.amount)
        first_name, family_name = self._split_name(user.full_name)
        fields: list[tuple[str, str]] = [
            ("IPCmethod", "IPCPurchase"),
            ("IPCVersion", self.ipc_version),
            ("IPCLanguage", self.language),
            ("SID", self.credentials.sid),
            ("WalletNumber", self.credentials.wallet_number),
            ("Amount", amount),
            ("Currency", order.currency),
            ("OrderID", order.order_code),
            ("KeyIndex", str(self.credentials.key_index)),
            ("URL_OK", f"{settings.base_url}{settings.api_v1_prefix}/payments/mypos/return/success"),
            ("URL_Cancel", f"{settings.base_url}{settings.api_v1_prefix}/payments/mypos/return/cancel"),
            ("URL_Notify", f"{settings.base_url}{settings.api_v1_prefix}/payments/mypos/notify"),
            ("PaymentMethod", self.payment_method),
            ("PaymentParametersRequired", str(self.payment_parameters_required)),
            ("Note", order.description[:127]),
            ("CartItems", "1"),
            ("Article_1", order.description[:127]),
            ("Quantity_1", "1"),
            ("Price_1", amount),
            ("Currency_1", order.currency),
            ("Amount_1", amount),
        ]

        if self.payment_parameters_required == 1:
            fields.extend(
                [
                    ("CustomerEmail", user.email),
                    ("CustomerFirstNames", first_name),
                    ("CustomerFamilyName", family_name),
                ]
            )

        if self.should_request_card_token:
            fields.append(("CardTokenRequest", "2"))
        else:
            fields.append(("CardTokenRequest", "0"))

        signature = self.sign_values(value for _, value in fields)
        fields.append(("Signature", signature))
        return fields

    def sign_values(self, values: Iterable[str]) -> str:
        message = base64.b64encode("-".join(values).encode("utf-8"))
        private_key = serialization.load_pem_private_key(
            self.credentials.private_key.encode("utf-8"),
            password=None,
        )
        signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode("ascii")

    def verify_payload(self, ordered_items: list[tuple[str, str]]) -> bool:
        signature = ""
        values: list[str] = []
        for key, value in ordered_items:
            if key == "Signature":
                signature = value
            else:
                values.append(value)

        if not signature:
            return False

        message = base64.b64encode("-".join(values).encode("utf-8"))
        try:
            certificate = x509.load_pem_x509_certificate(self.credentials.public_certificate.encode("utf-8"))
            certificate.public_key().verify(
                base64.b64decode(signature),
                message,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception:  # noqa: BLE001
            return False

        return True

    def get_order_amount(self) -> Decimal:
        if settings.mypos_monthly_amount is None:
            raise MyPosConfigurationError("Missing monthly amount configuration")
        return settings.mypos_monthly_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _format_amount(value: Decimal) -> str:
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _split_name(full_name: str | None) -> tuple[str, str]:
        if not full_name:
            return "OM", "Reader"

        parts = [item for item in full_name.strip().split() if item]
        if len(parts) == 1:
            return parts[0], parts[0]
        return " ".join(parts[:-1]), parts[-1]


mypos_service = MyPosService()
