"""Production entry point configured entirely through environment variables."""

from x402_openai.server import create_gateway, load_gateway_config

app = create_gateway(load_gateway_config())
