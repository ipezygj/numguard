# Your own x402-rs facilitator, config baked in. Deployed as a second Render service.
# FACILITATOR_PRIVATE_KEY (a dedicated gas wallet) is injected via env, never baked in.
FROM ghcr.io/x402-rs/x402-facilitator:latest
COPY facilitator/config.json /app/config.json
ENV CONFIG=/app/config.json
ENV HOST=0.0.0.0
# Render provides $PORT; the facilitator reads PORT (config host/port are overridden by env where supported).
EXPOSE 8080
