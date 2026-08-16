# Security policy

Report vulnerabilities privately through the repository security advisory feature. Do not open a
public issue containing wallet keys, API keys, payment signatures, facilitator credentials, or
reproduction steps that can move funds.

Supported releases receive security fixes on the latest minor version. Operators must store EVM
private keys, OpenAI keys, Redis credentials, HMAC secrets, and registry tokens in a secret manager.
Never enable debug body logging in production.
