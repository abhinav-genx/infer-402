"""Preflight: does the CDP x402 facilitator support `upto` on Base mainnet?

Generates a CDP EdDSA JWT from CDP_API_KEY_ID / CDP_API_KEY_SECRET and calls the
authenticated /supported endpoint. Free, read-only — no funds move.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time

import httpx
from nacl.signing import SigningKey

KEY_ID = os.environ["CDP_API_KEY_ID"]
SECRET_B64 = os.environ["CDP_API_KEY_SECRET"]
HOST = "api.cdp.coinbase.com"
PATH = "/platform/v2/x402/supported"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def cdp_jwt(method: str, host: str, path: str) -> str:
    seed = base64.b64decode(SECRET_B64)  # CDP Ed25519 secret = 32-byte seed + 32-byte pubkey
    signing_key = SigningKey(seed[:32])
    now = int(time.time())
    header = {"typ": "JWT", "alg": "EdDSA", "kid": KEY_ID, "nonce": secrets.token_hex(16)}
    payload = {
        "sub": KEY_ID,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uris": [f"{method} {host}{path}"],
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = signing_key.sign(signing_input.encode()).signature
    return f"{signing_input}.{_b64url(signature)}"


def main() -> None:
    token = cdp_jwt("GET", HOST, PATH)
    response = httpx.get(
        f"https://{HOST}{PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    print("CDP /supported ->", response.status_code)
    if response.status_code != 200:
        print(response.text[:600])
        return

    data = response.json()
    kinds = data.get("kinds", data if isinstance(data, list) else [])
    print(f"{len(kinds)} supported kinds:")
    upto_mainnet = False
    for kind in kinds:
        scheme = kind.get("scheme")
        network = kind.get("network")
        print("  ", {"x402Version": kind.get("x402Version"), "scheme": scheme, "network": network})
        if scheme == "upto" and network == "eip155:8453":
            upto_mainnet = True

    verdict = "SUPPORTED" if upto_mainnet else "NOT SUPPORTED"
    print("\n==> upto on Base mainnet (eip155:8453):", verdict)


if __name__ == "__main__":
    main()
