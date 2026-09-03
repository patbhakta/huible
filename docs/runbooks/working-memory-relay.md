# Runbook — TencentDB working-memory relay (W4 / HU-2470)

## What this is

The W4 chat-path working memory (BEAM Arm A port, HU-2309 v1.8 §1.7.2 /
M-0R-B) calls the TencentDB MemoryCore gateway (`/recall`, `/capture`) per
persona turn. The gateway (`tdai-memory-core.service`) binds **host loopback
only** (`127.0.0.1:8420`, `server.host` in
`/opt/tencentdb-memory/.config/tdai-gateway.yaml`). The `huible-app` container
lives on the `huible-net` compose bridge (`172.19.0.0/16`) and ufw's INPUT
default-deny blocks container → host-listener traffic, so the app cannot
reach the gateway directly.

Bridge in place (2026-09-03):

1. **`huible-tdai-relay.service`** (`/etc/systemd/system/`) — socat
   TCP relay bound to the bridge address **only**:
   `TCP-LISTEN:8420,bind=172.19.0.1,fork,reuseaddr → TCP:127.0.0.1:8420`.
   Not 0.0.0.0 — never publicly routable.
2. **ufw rule** (interface-scoped):
   `allow in on br-829dade6ca8c from 172.19.0.0/16 to 172.19.0.1 port 8420`
   (comment: `huible-app -> TencentDB working memory relay (W4 HU-2470)`).
3. **App env** (`.env`): `WORKING_MEMORY_ENABLED=on`,
   `WORKING_MEMORY_BASE_URL=http://172.19.0.1:8420`.

Trust note: the gateway currently has no API key armed (loopback-only
posture). The relay extends that trust to huible-net containers; if the
gateway arms `server.apiKey`, set `WORKING_MEMORY_API_KEY` in `.env` (the
client already sends the Bearer header).

## Health checks

```bash
systemctl is-active huible-tdai-relay
ss -ltn | grep 172.19.0.1:8420            # relay listening on bridge only
docker exec huible-app python -c \
  "import urllib.request;print(urllib.request.urlopen('http://172.19.0.1:8420/health',timeout=5).status)"
```

## Failure modes

- **Relay down** → app calls fail fast (connection refused), the W4 lane
  degrades to "no working memory this turn" (logged warning, trace shows
  `working_memory.chars=0`); chat turns stay healthy (pre-W4 behavior).
- **Gateway down** → same degraded lane via relay 500/ECONNREFUSED.
- **Latency budget**: relay adds <1 ms; recall/capture measured ~0.2–0.4 s
  each against the local gateway (E0 replay evidence).

## Bridge-name caveat

The ufw rule pins the compose bridge interface name
(`br-829dade6ca8c` for `huible-net` = subnet `172.19.0.0/16`). If the network
is ever recreated (`docker compose down` with network removal, project rename),
re-check:

```bash
docker network inspect huible_huible-net --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
ip -4 addr show | grep 172.19
ufw status | grep 8420
```

and update `bind=` in the unit + the ufw rule if the subnet/interface changed.

## Rollback

```bash
systemctl disable --now huible-tdai-relay
ufw status numbered   # delete the 8420 rule
# .env: WORKING_MEMORY_ENABLED=off && docker compose up -d app
```

The lane is inert when disabled — pre-W4 prompt shape, no relay traffic.
