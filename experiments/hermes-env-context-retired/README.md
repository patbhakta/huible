# Retired: Hermes Pat-hardcoded env-context refresher (HU-2284)

Retired 2026-08-31. This is a verbatim archive of
`/root/.hermes/scripts/env-context-refresh.py` (monthly cron
`946bc5660068` "env-context-refresh"), which wrote only the single
Pat/Phoenix atom `env_context_pat` with hardcoded coordinates.

Switchover (HU-2284, follow-up to HU-2194 commit 112b230):

- Cron `946bc5660068` now runs `/root/.hermes/scripts/env-context-refresh.sh`,
  a thin wrapper that execs
  `/root/repos/huible/.venv/bin/python /root/repos/huible/scripts/env_context_refresh.py --all`
  (the per-client registry tool over `modules/onboarding/env_context.py`
  + `onboarding/env-context-clients.json`).
- Contract preserved: silent on success, nonzero exit on failure; key
  resolution unchanged (env `TDAI_API_KEY` > local metadata DB > `local`).
- Verified green through the real scheduler path on 2026-08-31T14:26:27Z
  (`last_status: ok`), plus a live v3/atomic/search roundtrip with
  `env_context_pat` as the #1 hit for the user scope.

The Hermes cron runner sandboxes job scripts to `~/.hermes/scripts/`, so the
wrapper must live there; the huible repo owns the actual implementation.
