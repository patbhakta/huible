# H4 — Five-Friends blind test kit (PACKAGED, not run)

## Governance
- Runs ONLY when the boss chooses: `python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run`
- Boss is the sole rater until he lifts the hold. NO external humans.
- The kit never self-grades; its only output is blind pairs + this rating flow.

## Contents
- `personas.json` — the five vault-grounded persona slots + provisioning state
- `rating_form.md` — the §1.7.4 v0 blind-rating axes (boss is the rater)
- `run_kit.sh` — generation → pairing (seeded, recorded) → rating-packager

## Flow (when the boss chooses to run)
1. Provision slots 2-5 (boss casting + vault onboarding) or run with slot 1 only.
2. `run_kit.sh` replays the frozen script per persona through the real-user path
   AND generates a comparator arm (E0-baseline persona), then pairs transcripts
   with a recorded-seed shuffle and emits a rating pack per pair.
3. The boss rates offline. Nothing in the pipeline assigns or implies a verdict.
