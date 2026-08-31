# §3a.4 remaining items — served demo + gates (2026-08-31, TL sprint)

## Served demo (Caddy static, tailnet-only)

**URL:** `http://100.101.235.117:8099` (tailscale devices only — same posture as the :8098 demo bridge; UFW blocks the port off-tailnet, and Caddy binds the tailscale IP explicitly).

- Site block appended to `/etc/caddy/Caddyfile` (backup: `Caddyfile.bak.20260831-HU2162`).
- Root = this app's `build/web` — rebuilds (`flutter build web --release`) are picked up on refresh, no Caddy touch.
- Rebuilt with `--dart-define=API_BASE_URL=https://api.huible.com` so the served build targets the **live prod §7.4 API** out of the box (settings screen can still override at runtime; `API_BASE_URL` is just the compile-time default, localhost fallback preserved for `flutter run`).
- COOP/COEP headers pre-staged per plan §3a.3 (future multithreaded-wasm build; all assets same-origin today).

First-use on the demo: Settings → paste a valid API key (persona allowlist applies server-side; `demo-pat` canary works) → chat. First turn exercises the real 409 → consent card → ack → streamed reply flow proven in M2.

## Pre-commit hook

`.githooks/pre-commit` — activates with `git config core.hooksPath .githooks` (run once per clone). Only fires when staged files touch `experiments/flutter-ignite/**` Dart/pubspec sources; runs `flutter analyze` + `flutter test` scoped to the changed app(s); fails open if the SDK is absent (CI is the authoritative gate).

## CI build step

`.github/workflows/flutter-app-ci.yml` — path-filtered on `experiments/flutter-ignite/**`: pub get → analyze → test → `flutter build web --release` for each app dir in the matrix.

## Verification (this sprint)

- `flutter analyze`: no new findings (12 pre-existing infos/warnings all in `tool/live_probe.dart`, the diagnostic harness)
- `flutter test`: **26/26** (2 new: compile-time default fallback + copyWith preservation)
- `flutter build web --release` with dart-define: OK; `api.huible.com` string present in compiled bundle
- Served asset checks: `/`, `/main.dart.js`, `/flutter.js`, `/canvaskit/canvaskit.js` all 200; COOP/COEP headers present
