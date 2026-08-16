from __future__ import annotations

import re
from decimal import Decimal
from typing import Final, Literal, TypeAlias

BASE_MAINNET: Final = "eip155:8453"
BASE_SEPOLIA: Final = "eip155:84532"
SupportedNetwork: TypeAlias = Literal["eip155:8453", "eip155:84532"]

USDC_BY_NETWORK: Final[dict[SupportedNetwork, str]] = {
    BASE_MAINNET: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    BASE_SEPOLIA: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}

_USD_PATTERN = re.compile(r"^\d+(?:\.\d{1,6})?$")


def parse_usd_to_atomic(value: str) -> int:
    normalized = value.removeprefix("$")
    if not _USD_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid USD amount: {value}")
    return int(Decimal(normalized) * 1_000_000)
