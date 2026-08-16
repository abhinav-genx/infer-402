import { x402Client } from "@x402/core/client";
import { ExactEvmScheme } from "@x402/evm/exact/client";
import { UptoEvmScheme } from "@x402/evm/upto/client";
import { appendPaymentIdentifierToExtensions } from "@x402/extensions/payment-identifier";
import { wrapFetchWithPayment } from "@x402/fetch";
import { privateKeyToAccount } from "viem/accounts";

import {
  BASE_MAINNET,
  BASE_SEPOLIA,
  parseUsdToAtomic,
  USDC_BY_NETWORK,
} from "@infer402/core";

import type { PaymentSigner, X402OpenAIOptions } from "./types.js";

export function privateKeySigner(privateKey: `0x${string}`): PaymentSigner {
  return privateKeyToAccount(privateKey);
}

export function createPaymentFetch(
  options: Required<Pick<X402OpenAIOptions, "fetch">> & X402OpenAIOptions,
  paymentId: string,
): typeof globalThis.fetch {
  const networks = options.networks ?? [BASE_MAINNET, BASE_SEPOLIA];
  const maximumAtomic = parseUsdToAtomic(options.maxPaymentUsd ?? "$1.00");
  const exact = new ExactEvmScheme(options.signer);
  const upto = new UptoEvmScheme(options.signer);
  const schemes = networks.flatMap((network) => [
    { network, client: exact },
    { network, client: upto },
  ]);

  const payments = x402Client.fromConfig({
    schemes,
    policies: [
      (_version, requirements) =>
        requirements.filter((requirement) => {
          const network = networks.find((candidate) => candidate === requirement.network);
          if (!network) return false;
          if (requirement.asset.toLowerCase() !== USDC_BY_NETWORK[network].toLowerCase()) {
            return false;
          }

          try {
            return BigInt(requirement.amount) <= maximumAtomic;
          } catch {
            return false;
          }
        }),
    ],
  });

  payments.onBeforePaymentCreation(({ paymentRequired }) => {
    if (paymentRequired.extensions) {
      appendPaymentIdentifierToExtensions(paymentRequired.extensions, paymentId);
    }
    return Promise.resolve();
  });

  return wrapFetchWithPayment(options.fetch, payments);
}
