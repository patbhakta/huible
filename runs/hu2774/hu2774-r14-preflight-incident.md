# HU-2774 preflight incident: engine silently in mock generator mode (fixed 2026-09-13 ~02:45Z)

Recorded by Huible PM run (agent c1fa8720) during the 02:36Z heartbeat, after the
02:35Z r12b/r13 verdict landing. No repo code changed; .env + running container only.

## What happened

- 02:11Z the `huible-app` docker container came back up with `generator: ready (mock)`
  (health endpoint). r14 at 10:05Z would have scored a mock voice and burned the slot.
- Root cause: `.env:19` had `GENERATOR_PROVIDER=openrouter` — not a valid
  `GeneratorProvider` value (`mock` | `openai_compatible` only,
  src/huible/persona/generator.py). Unknown values fall back to the mock default
  silently by design ("a misconfiguration can never silently wire a real model" —
  it does silently wire the mock instead).
- Provenance: same 23:59:16Z rebuild that touched `.env` (ZAI_DAILY_TOKEN_LIMIT
  8M raise, mtime 00:00:53Z) and the reverted gemini/openrouter voice-swap attempt
  (commit 1b442c2). The swap revert fixed the base URL/model/key back to zai but
  left the provider value behind. r12b (00:46) and r13 (01:11) ran real because the
  container then still carried the older, correct env; the 02:11Z recreate was the
  first to ingest the bad line.

## Fix + verification

- `.env:19` -> `GENERATOR_PROVIDER=openai_compatible` (zai endpoint is
  OpenAI-compatible; BASE_URL/MODEL/API_KEY were already the proven zai config).
- `docker compose up -d app` (recreate). Health now: `generator: ready`, database ok,
  pgvector ok. Container env confirms `GENERATOR_PROVIDER=openai_compatible`.
- App logs post-restart: zero errors; persona registry hydrated 6 persona(s) at
  02:41:59Z. Dynamics v5 confirmed in DB for both battery personas (voice_instructions
  contain the creator/programmer/developer ban): Chandler fdc3a44b, Monica 3ef60bec.
- Durable property: `.env` is now correct, so ANY future container restart lands real.
- r14's own preflight probe at 10:05Z is the end-to-end real-voice check.

## Not done here / owners

- Why the container restarted at 02:11Z (mid Tech Lead run) is unattributed — if it
  recurs, look at compose restart policy / the v5-apply restart step timing.
- r14 read stays with the Tech Lead at the 10:45Z monitor wake (HU-2837 monitor,
  nextCheckAt 10:45:00Z, timeout 14:30Z, maxAttempts 3).
- ZAI_DAILY_TOKEN_LIMIT=8M disposition unchanged: TEMPORARY, revert to 2M when the
  3-run battery bar is met, unless the founder ratifies (PM-verified via HU-2847).
