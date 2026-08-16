from __future__ import annotations

from typing import Any

from eth_account import Account
from x402.mechanisms.evm import EthAccountSigner


def private_key_signer(private_key: str) -> Any:
    if not private_key.startswith("0x") or len(private_key) != 66:
        raise ValueError("private_key must be a 32-byte 0x-prefixed hex string")
    return EthAccountSigner(Account.from_key(private_key))
