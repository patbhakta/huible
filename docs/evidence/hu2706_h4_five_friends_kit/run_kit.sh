#!/usr/bin/env bash
# Five-Friends blind test — EXECUTION GATED: refuses without --i-am-the-boss.
set -euo pipefail
case "${1:-}" in
  --i-am-the-boss) shift ;;
  *) echo 'REFUSED: the Five-Friends test runs only when the boss chooses.' \
     echo 'Usage: run_kit.sh --i-am-the-boss [--persona <uuid> ...]' >&2; exit 3 ;;
esac
# Phase 1 (per provisioned persona): frozen-script replay + comparator arm.
# Phase 2: seeded pairing + rating packager. Implemented as:
#   python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run \
#       --persona <uuid> [--persona <uuid> ...]
python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run "$@"
