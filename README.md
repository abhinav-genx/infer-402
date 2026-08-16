# x402 OpenAI SDKs

Production-oriented TypeScript and Python SDKs for OpenAI-compatible Responses API calls paid in
USDC through x402 on Base or Base Sepolia.

## Install

TypeScript buyer:

```bash
npm install @infer402/client
```

TypeScript provider:

```bash
npm install @infer402/server
```

Python buyer:

```bash
pip install x402-openai
```

Python provider:

```bash
pip install "x402-openai[server]"
```

Packages publish under the `@infer402` npm scope and the `infer402` GitHub org. Confirm the PyPI
project name is available or choose your organization-specific name.

## Repository map

- `typescript/`: npm workspaces for buyer, server, shared core, and deployable gateway.
- `python/`: one typed PyPI distribution with sync/async clients and optional FastAPI server.
- `specs/`: language-neutral schemas, pricing rules, and conformance fixtures.
- `tests/interoperability/`: cross-language deployment and live-test instructions.
- `apps/`: production container definitions.

## Protocol guarantees

Both implementations:

- support x402 `exact` and `upto` on the buyer;
- use `upto` for provider-side usage billing;
- accept only Base or Base Sepolia and their canonical USDC contracts;
- enforce buyer-side per-call limits before signing;
- calculate prices with decimal arithmetic and six-decimal USDC atomic units;
- propagate idempotency and OpenAI request identifiers;
- reject streaming because settlement depends on final token usage;
- cap the final settlement at the buyer's authorized maximum.

## Development

```bash
cd typescript
npm ci
npm run check
npm run pack:check

cd ../python
python -m venv .venv
. .venv/bin/activate
pip install -e ".[server,dev]"
ruff check .
mypy src
pytest
python -m build
```

## Run either gateway

Copy `.env.example` to `.env`, replace every placeholder, then start Redis and one gateway:

```bash
docker compose -f tests/interoperability/docker-compose.yml up --build redis typescript-gateway
# or: docker compose -f tests/interoperability/docker-compose.yml up --build redis python-gateway
```

Both expose the same paid endpoint at `POST /v1/responses`; the TypeScript service listens on
`localhost:4021` and the Python service is mapped to `localhost:4022`.

## Deployment boundary

The example prices are intentionally non-authoritative. Replace them with a reviewed, versioned
pricing table. Use the public `x402.org` facilitator only for Base Sepolia testing. Base mainnet
requires a production facilitator. Run live tests with a funded buyer wallet before accepting real
payments.

`X-Client-Request-Id` improves traceability but is not an upstream billing idempotency guarantee. A
connection failure after OpenAI processes a request can still create upstream provider cost.
