"""One-off: swap a small amount of native ETH -> USDC on Base mainnet (Uniswap V3).

Real transaction. Quotes first, applies slippage protection, leaves ETH for gas.
    EVM_PRIVATE_KEY=0x... SWAP_ETH=0.0005 python scripts/swap_eth_usdc.py
"""

from __future__ import annotations

import os
from decimal import Decimal

from eth_account import Account
from web3 import Web3

RPC = "https://mainnet.base.org"
WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
ROUTER = Web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")  # SwapRouter02
QUOTER = Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")  # QuoterV2
FEE = 500  # 0.05% WETH/USDC pool
SWAP_ETH = Decimal(os.environ.get("SWAP_ETH", "0.0005"))

ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]
QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "sqrtPriceX96After", "type": "uint160"},
            {"name": "initializedTicksCrossed", "type": "uint32"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "o", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]


def main() -> None:
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))
    acct = Account.from_key(os.environ["EVM_PRIVATE_KEY"])
    addr = acct.address
    usdc = w3.eth.contract(address=USDC, abi=ERC20_ABI)

    eth_balance = w3.eth.get_balance(addr)
    amount_in = w3.to_wei(SWAP_ETH, "ether")
    print(f"Account: {addr}")
    print(f"ETH balance: {w3.from_wei(eth_balance, 'ether')}  | swapping {SWAP_ETH} ETH")
    print(f"USDC before: {usdc.functions.balanceOf(addr).call() / 1e6:.6f}")
    if amount_in >= eth_balance:
        raise SystemExit("Not enough ETH (need to keep some for gas).")

    quoter = w3.eth.contract(address=QUOTER, abi=QUOTER_ABI)
    quoted_out = quoter.functions.quoteExactInputSingle((WETH, USDC, amount_in, FEE, 0)).call()[0]
    min_out = quoted_out * 97 // 100  # 3% slippage guard
    print(f"Quote: ~{quoted_out / 1e6:.6f} USDC  | min accepted {min_out / 1e6:.6f} USDC")

    router = w3.eth.contract(address=ROUTER, abi=ROUTER_ABI)
    params = (WETH, USDC, FEE, addr, amount_in, min_out, 0)
    tx = router.functions.exactInputSingle(params).build_transaction(
        {
            "from": addr,
            "value": amount_in,
            "nonce": w3.eth.get_transaction_count(addr),
            "chainId": 8453,
            "gas": 300_000,
            "gasPrice": int(w3.eth.gas_price * Decimal("1.2")),
        }
    )
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"swap tx: https://basescan.org/tx/{tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    print("status:", "success" if receipt.status == 1 else "FAILED")
    print(f"USDC after: {usdc.functions.balanceOf(addr).call() / 1e6:.6f}")
    print(f"ETH after : {w3.from_wei(w3.eth.get_balance(addr), 'ether')}")


if __name__ == "__main__":
    main()
