import { privateKeyToAccount } from "viem/accounts";
import { describe, expect, it } from "vitest";

import { X402OpenAI } from "../src/client.js";

const signer = privateKeyToAccount(
  "0x0000000000000000000000000000000000000000000000000000000000000001",
);

describe("X402OpenAI", () => {
  it("rejects relative provider URLs", () => {
    expect(() => new X402OpenAI({ baseURL: "/v1", signer })).toThrow(
      "baseURL must be an absolute URL",
    );
  });

  it("rejects streaming before payment", async () => {
    const client = new X402OpenAI({ baseURL: "https://provider.example/v1", signer });
    await expect(
      client.responses.create({ model: "example", input: "hello", stream: true } as never),
    ).rejects.toMatchObject({ code: "VALIDATION_ERROR" });
  });

  it("returns a successful provider response", async () => {
    const fetchMock: typeof fetch = (input, init) => {
      const request = new Request(input, init);
      expect(request.url).toBe("https://provider.example/v1/responses");
      expect(request.headers.get("idempotency-key")).toBe("req_1234567890abcdef");
      return Promise.resolve(
        new Response(
          JSON.stringify({ id: "resp_1", object: "response", output: [], output_text: "hello" }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    };
    const client = new X402OpenAI({
      baseURL: "https://provider.example/v1",
      signer,
      fetch: fetchMock,
    });

    const result = await client.responses.create(
      { model: "example", input: "hello" },
      { idempotencyKey: "req_1234567890abcdef" },
    );

    expect(result.data.output_text).toBe("hello");
  });
});
