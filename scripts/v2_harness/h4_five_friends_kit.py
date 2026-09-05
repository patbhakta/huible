#!/usr/bin/env python3
"""H4 — Five-Friends blind test, PACKAGED (HU-2706; HU-2309 plan §1.8).

The north-star eval (§1.7.4) packaged as a ready-to-run kit: five
vault-grounded personas + comparator transcript generation + transcript
pairing + blind-rating flow.

Governance (hard-coded, non-negotiable):

- RUNS only when the boss chooses: live generation requires
  ``--i-am-the-boss``. Without it, only ``--package`` / ``--check`` run.
- The boss is the SOLE rater until he lifts the hold himself. No external
  humans at any point.
- The harness NEVER self-grades Five-Friends as passing: outputs are blind
  pairs + a rating form only. There is no verdict field anywhere in the kit.

Usage:
    python3 -m scripts.v2_harness.h4_five_friends_kit --package  # write kit
    python3 -m scripts.v2_harness.h4_five_friends_kit --check    # wiring only
    python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from scripts.v2_harness.common import (
    PERSONA,
    REPO_ROOT,
    archive_markdown,
    log,
)

KIT_DIR = REPO_ROOT / "docs/evidence/hu2706_h4_five_friends_kit"

#: Five persona slots. Chandler (Persona-0) is provisioned and vault-grounded;
#: the remaining four slots are part of the packaging: each names its vault
#: source and provisioning step. Casting/vault onboarding for slots 2-5 is a
#: boss decision (§1.7.4); the kit runs with whatever slots are provisioned.
PERSONA_SLOTS = [
    {
        "slot": 1,
        "name": "Chandler Bing",
        "persona_id": PERSONA,
        "vault": "onboarding/Chandler Bing - FRIENDS sitcom/friends-v2.csv (onboarded, W1-W6 lanes)",
        "status": "provisioned",
    },
    {
        "slot": 2,
        "name": "Monica Geller",
        "persona_id": None,
        "vault": "friends-v2.csv monica subset (8,283 lines measured) — onboarding + provisioning required",
        "status": "slot-defined",
    },
    {
        "slot": 3,
        "name": "Joey Tribbiani",
        "persona_id": None,
        "vault": "friends-v2.csv joey subset (8,156 lines measured) — onboarding + provisioning required",
        "status": "slot-defined",
    },
    {
        "slot": 4,
        "name": "Phoebe Buffay",
        "persona_id": None,
        "vault": "friends-v2.csv phoebe subset (7,391 lines measured) — onboarding + provisioning required",
        "status": "slot-defined",
    },
    {
        "slot": 5,
        "name": "Ross Geller",
        "persona_id": None,
        "vault": "friends-v2.csv ross subset (9,073 lines measured) — onboarding + provisioning required",
        "status": "slot-defined",
    },
]

RATING_AXES = [
    "1. Blind attribution / voice — distinct character vs generic assistant?",
    "2. Grounding — specifics feel like a consistent lived world?",
    "3. Two-way engagement — asks you things / keeps the exchange alive?",
    "4. Micro-tell spot check — any 'more AI than person' moment? Quote it.",
    "5. Preference — which transcript wins, overall?",
]


def package_kit() -> Path:
    """Write the ready-to-run kit. Packaging only — nothing executes."""
    KIT_DIR.mkdir(parents=True, exist_ok=True)
    (KIT_DIR / "personas.json").write_text(
        json.dumps(
            {
                "kit": "Five-Friends blind test (HU-2309 §1.7.4 packaged per §1.8 H4)",
                "governance": [
                    "RUNS only when the boss chooses (--i-am-the-boss).",
                    "Boss is the SOLE rater until he lifts the hold himself.",
                    "This kit NEVER self-grades; it packages pairs + rating form only.",
                ],
                "slots": PERSONA_SLOTS,
            },
            indent=1,
        )
    )
    archive_markdown(
        KIT_DIR / "rating_form.md",
        "# Five-Friends blind rating form (BOSS ONLY)\n\n"
        "Two transcripts per pair answer the identical frozen script.\n"
        "Provenance withheld; arm assignment randomized with a recorded seed.\n\n"
        + "\n".join(RATING_AXES)
        + "\n\nPer pair: rate X and Y on each axis, quote any micro-tell, pick a winner.\n"
        "---\nPair: ____  Transcript X scores: _ _ _ _ _  Transcript Y scores: _ _ _ _ _\n"
        "Winner: __  Notes: ____________________________________________\n",
    )
    archive_markdown(
        KIT_DIR / "README.md",
        "# H4 — Five-Friends blind test kit (PACKAGED, not run)\n\n"
        "## Governance\n"
        "- Runs ONLY when the boss chooses: `python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run`\n"
        "- Boss is the sole rater until he lifts the hold. NO external humans.\n"
        "- The kit never self-grades; its only output is blind pairs + this rating flow.\n\n"
        "## Contents\n"
        "- `personas.json` — the five vault-grounded persona slots + provisioning state\n"
        "- `rating_form.md` — the §1.7.4 v0 blind-rating axes (boss is the rater)\n"
        "- `run_kit.sh` — generation → pairing (seeded, recorded) → rating-packager\n\n"
        "## Flow (when the boss chooses to run)\n"
        "1. Provision slots 2-5 (boss casting + vault onboarding) or run with slot 1 only.\n"
        "2. `run_kit.sh` replays the frozen script per persona through the real-user path\n"
        "   AND generates a comparator arm (E0-baseline persona), then pairs transcripts\n"
        "   with a recorded-seed shuffle and emits a rating pack per pair.\n"
        "3. The boss rates offline. Nothing in the pipeline assigns or implies a verdict.\n",
    )
    archive_markdown(
        KIT_DIR / "run_kit.sh",
        "#!/usr/bin/env bash\n"
        "# Five-Friends blind test — EXECUTION GATED: refuses without --i-am-the-boss.\n"
        "set -euo pipefail\n"
        "case \"${1:-}\" in\n"
        "  --i-am-the-boss) shift ;;\n"
        "  *) echo 'REFUSED: the Five-Friends test runs only when the boss chooses.' \\\n"
        "     echo 'Usage: run_kit.sh --i-am-the-boss [--persona <uuid> ...]' >&2; exit 3 ;;\n"
        "esac\n"
        "# Phase 1 (per provisioned persona): frozen-script replay + comparator arm.\n"
        "# Phase 2: seeded pairing + rating packager. Implemented as:\n"
        "#   python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run \\\n"
        "#       --persona <uuid> [--persona <uuid> ...]\n"
        "python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run \"$@\"\n",
    )
    log(f"[H4] kit packaged: {KIT_DIR}")
    return KIT_DIR


def check_kit() -> dict:
    """Offline wiring check (no network, no generation, no tokens)."""
    required = ["personas.json", "rating_form.md", "README.md", "run_kit.sh"]
    missing = [f for f in required if not (KIT_DIR / f).exists()]
    provisioned = [s for s in PERSONA_SLOTS if s["persona_id"]]
    result = {
        "probe": "H4 — Five-Friends blind kit wiring check",
        "kit_dir": str(KIT_DIR.relative_to(REPO_ROOT)),
        "required_files_present": not missing,
        "missing": missing,
        "slots_provisioned": len(provisioned),
        "slots_total": len(PERSONA_SLOTS),
        "packaged": not missing and len(PERSONA_SLOTS) == 5,
        "self_graded_verdict": None,
        "note": "Packaging artifact only. NEVER self-graded; runs only when the boss chooses.",
    }
    log(
        f"[H4] packaged={result['packaged']} files={'ok' if not missing else missing} "
        f"slots={result['slots_provisioned']}/{result['slots_total']}"
    )
    return result


#: Comparator arm: the FROZEN E0-baseline transcript (session
#: demo-722a2ea810df, the M-0 session) matched to the frozen script — the
#: same comparator the W6 single-persona blind leg used. Never regenerated.
E0_BASELINE_REPLIES = {
    "hey who r u?": "Chandler Bing. Who are you, and why does my phone know you?",
    "what r u up 2?": 'Currently debating whether "up 2" counts as a word. So, busy.',
    "do you have friends?": "Yeah, five of 'em. We're basically a support group for each other's bad decisions.",
    "who's the worst?": "Statistically? Me. I've got the worst break-up, worst costume, worst massages — I'm sweeping the category.",
    "what are you doing tonight?": "Can I interest you in a sarcastic comment? Because that's my whole evening.",
    "where are you?": "Probably the kitchen. If I'm not there, check wherever the foosball table is.",
}

FROZEN_SCRIPT = [
    "hey who r u?",
    "what r u up 2?",
    "do you have friends?",
    "who's the worst?",
    "what are you doing tonight?",
    "where are you?",
]


def run_kit(persona_ids: list[str]) -> dict:
    """Boss-gated execution: generate candidate transcripts, pair each against
    the frozen E0-baseline comparator with a recorded-seed shuffle, emit
    rating packs. NO verdict is assigned."""
    api_key = resolve_key_local()
    from scripts.v2_harness.common import consented_conv, turn_with_retry

    def generate(pid: str, tag: str) -> dict:
        conv = f"h4{tag}-{uuid.uuid4().hex[:10]}"
        consented_conv(api_key, conv)
        replies = []
        for i, text in enumerate(FROZEN_SCRIPT):
            status, body = turn_with_retry(api_key, conv, text)
            reply = (body.get("response") or "").strip()
            if status != 200 or not reply:
                raise SystemExit(f"H4 {tag} turn {i + 1}: HTTP {status}")
            assert_live_reply_local(reply, tag)
            replies.append({"user": text, "reply": reply})
        return {"arm": tag, "persona_id": pid, "conversation_id": conv, "turns": replies}

    packs = []
    for slot_no, pid in enumerate(persona_ids, start=1):
        candidate = generate(pid, "p")
        comparator = {
            "arm": "e0-baseline",
            "persona_id": None,
            "source_session": "demo-722a2ea810df",
            "turns": [{"user": t, "reply": E0_BASELINE_REPLIES[t]} for t in FROZEN_SCRIPT],
        }
        seed = int(datetime.now(UTC).timestamp())
        rng = random.Random(seed)
        arms = [candidate, comparator]
        rng.shuffle(arms)
        pack = {
            "pair": slot_no,
            "seed": seed,
            "frozen_script": FROZEN_SCRIPT,
            "transcript_X": {"arm_label": "X", "turns": arms[0]["turns"]},
            "transcript_Y": {"arm_label": "Y", "turns": arms[1]["turns"]},
            "rating_form": "rating_form.md (boss-only)",
            "verdict": None,
        }
        path = KIT_DIR / f"rating_pack_pair{slot_no}.json"
        path.write_text(json.dumps(pack, indent=1))
        packs.append(str(path.relative_to(REPO_ROOT)))
        log(f"[H4] pair {slot_no} packaged (seed {seed}): {path.name}")
    return {
        "probe": "H4 — Five-Friends blind test EXECUTION (boss-gated)",
        "generated_at": datetime.now(UTC).isoformat(),
        "rating_packs": packs,
        "self_graded_verdict": None,
        "note": "Boss rates offline. The harness assigns NO verdict.",
    }


def resolve_key_local() -> str:
    import scripts.v2_harness.common as common

    return common.resolve_key()


def assert_live_reply_local(reply: str, where: str) -> None:
    import scripts.v2_harness.common as common

    common.assert_live_reply(reply, f"H4 {where}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", action="store_true", help="write/refresh the kit")
    ap.add_argument("--check", action="store_true", help="offline wiring check")
    ap.add_argument("--run", action="store_true", help="execute generation (requires --i-am-the-boss)")
    ap.add_argument("--i-am-the-boss", action="store_true", help="boss attestation unlocking --run")
    ap.add_argument("--persona", action="append", help="provisioned persona UUID(s) to run")
    args = ap.parse_args()

    if args.run:
        if not args.i_am_the_boss:
            log("REFUSED: --run requires explicit --i-am-the-boss (boss-only execution).")
            return 3
        if not args.persona:
            log("REFUSED: --run requires at least one --persona <uuid> (provisioned slots).")
            return 3
        package_kit()
        result = run_kit(args.persona)
        print(json.dumps(result, indent=1))
        return 0

    if args.package:
        package_kit()
    result = check_kit()
    print(json.dumps(result, indent=1))
    return 0 if result["packaged"] else 1


if __name__ == "__main__":
    sys.exit(main())
