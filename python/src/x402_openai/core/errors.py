from __future__ import annotations

from typing import Any


class X402OpenAIError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


class ConfigurationError(X402OpenAIError):
    def __init__(self, message: str) -> None:
        super().__init__("CONFIGURATION_ERROR", message)


class PaymentRejectedError(X402OpenAIError):
    def __init__(self, message: str, status: int = 402) -> None:
        super().__init__("PAYMENT_REJECTED", message, status)
