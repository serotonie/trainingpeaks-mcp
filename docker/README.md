# Docker HTTP deployment

This fork adds native Streamable HTTP using the MCP SDK's ASGI transport.
No stdio subprocess or bridge is used. GitHub Actions builds and tests
`linux/amd64` and `linux/arm64` images, then publishes them to
`ghcr.io/serotonie/trainingpeaks-mcp:latest` and an immutable `sha-<commit>` tag.
No local image build is needed on the deployment infrastructure.

The image installs the fork's own source. Runtime dependencies are pinned with
hashes in `requirements.txt`; `requirements.in` declares direct dependencies.
Use Python 3.12 when regenerating that lock.

## Runtime

- Run as UID/GID `1000:1000`, drop all capabilities and use a read-only root.
- Mount a writable tmpfs at `/tmp` (128 MiB is sufficient for offline mode).
- Attach only to the internal Docker Swarm MCP overlay, with service name
  `trainingpeaks`. No host port is necessary.
- MCP endpoint: `http://trainingpeaks:3000/mcp`.
- Health endpoint: `http://trainingpeaks:3000/healthz`.
- `TP_LIVE_ENABLED=false` by default: tool discovery and UI resources work,
  but every tool call returns an explicit `LIVE_ACCESS_DISABLED` error without
  invoking upstream handlers. Startup never validates credentials remotely.
- `TP_READ_ONLY=true` by default: only upstream tools annotated read-only are
  advertised. Direct calls to write tools are rejected before dispatch, even
  when the caller ignores discovery. This does not make the cookie itself
  read-only: it remains a full-account credential.
- For Docker secrets, set `TP_AUTH_COOKIE_FILE=/run/secrets/trainingpeaks_auth_cookie`.
  The cookie is read at startup without printing it or placing it in the service
  specification. An empty or unreadable file fails startup.
- Health reports `readOnly`, `tmpUsedBytes` and `tmpCapacityBytes` to measure
  temporary storage during read workloads. Downloads and analysis can write
  files; capacity measured for JSON reads does not size those workloads.
- No HTTP authentication is configured; do not expose this service publicly.
- Live use is a separate operation: supply `TP_AUTH_COOKIE` through your
  orchestrator's secret mechanism and set `TP_LIVE_ENABLED=true`. Startup fails
  if live mode is enabled without a cookie. Never commit credentials.

Unit and HTTP protocol tests run in the image build without contacting
TrainingPeaks. The health endpoint reports the live-access flag so a healthy
service is not mistaken for an authenticated TrainingPeaks connection.
