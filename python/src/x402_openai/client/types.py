from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from x402_openai.core import PaymentReceipt

ResponseT = TypeVar("ResponseT")


@dataclass(frozen=True, slots=True)
class PaidResponse(Generic[ResponseT]):
    data: ResponseT
    idempotency_key: str
    payment_response: str | None = None
    receipt: PaymentReceipt | None = None


ResponseBody = dict[str, Any]
ResponseResult = PaidResponse[dict[str, Any]]
