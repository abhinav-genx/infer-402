export const BASE_MAINNET = "eip155:8453" as const;
export const BASE_SEPOLIA = "eip155:84532" as const;

export const SUPPORTED_NETWORKS = [BASE_MAINNET, BASE_SEPOLIA] as const;
export type SupportedNetwork = (typeof SUPPORTED_NETWORKS)[number];

export const USDC_BY_NETWORK: Readonly<Record<SupportedNetwork, `0x${string}`>> = {
  [BASE_MAINNET]: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
  [BASE_SEPOLIA]: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
};

export function isSupportedNetwork(value: string): value is SupportedNetwork {
  return SUPPORTED_NETWORKS.some((network) => network === value);
}

/** Convert a decimal USD/USDC value to six-decimal atomic units without binary floats. */
export function parseUsdToAtomic(value: string): bigint {
  const normalized = value.startsWith("$") ? value.slice(1) : value;
  if (!/^\d+(?:\.\d{1,6})?$/.test(normalized)) {
    throw new Error(`Invalid USD amount: ${value}`);
  }

  const [whole = "0", fraction = ""] = normalized.split(".");
  return BigInt(whole) * 1_000_000n + BigInt(fraction.padEnd(6, "0"));
}
