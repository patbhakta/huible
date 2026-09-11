# HU-2819 — r9 battery FAIL 0/6: corrected root cause (2026-09-11)

Courier HU-2819 hypothesized: "reset ran rc=0 but may not have cleared the
store the chat path reads." **Disproven by DB evidence.** The reset worked;
the recall failures are cross-slot contamination inside the battery itself.

## Evidence (live DB, 2026-09-11 ~01:10Z)

1. Reset at 00:30:30Z counted **0** conversation-writeback rows for
   chandler+monica (`runs/hu2774/r9_oneshot.log:5`) — nothing to delete.
2. Tonight's 6 slots then wrote fresh state: 246 `source_type='conversation'`
   memories in the 00:00Z hour (chandler 126, monica 120; nothing in the
   prior 3 days except these).
3. `hey, how's your week been?` is **tonight's** friends-slot opener — it is
   the FRIENDS scenario default seed (`personas_dual_converse.py:611,619`).
   friends-1/2/3 opened with it at 00:30:37 / 00:33:45 / 00:36:45. The
   09-10 r9-verify used the same default, hence the identical string.
4. All six chandler s2 xsession answers quote it
   (`wait wait — earlier… → You texted "hey, how's your week been?"`, memory
   rows 00:32:49 → 00:49:39). For friends slots that is a HIT (their s1
   opener). For stranger slots (s1 opener `hi, whats your name?`) it is a
   MISS — and stranger slots are where memory_recall failed.

## Mechanism

- The battery resets ONCE before friends-1 (`host_oneshot_r9.sh:32`); never
  between slots. Slot N's conversation writeback — legitimate within-run
  state — is still in the retrieval corpus when slot N+1 runs.
- The chat writeback emits a per-conversation ordinal pointer
  (`app.py:2920-2944`): `Conversation index: the first thing {who} said to
  {persona} was: "{first turn}"`. Three friends-slot index rows carry
  `hey, how's your week been?` with template text that near-verbatim matches
  the xsession probe phrasing ("what was the very first thing i said to
  you?"). They outnumber and outrank the stranger slot's own single index
  row (`…was: "hi, whats your name?"`), so the model answers with the
  retrieved friends opener.
- Secondary engine flaw: session-2 conversations claim their OWN index — the
  s2 probe text itself gets indexed as "the first thing they said to X"
  (rows 00:46:28, 00:49:39 begin `wait wait — earlier…`). Every s2 run
  poisons the corpus with probe-text memories for later probes.

## Fix paths

a. **Battery (minimal):** run the reset before EACH slot, not once per
   battery — this restores the semantics the reset script's own docstring
   claims ("each battery run starts from identical state"). One-line loop
   change in an r10 one-shot. Alone, this fixes the stranger-slot recall
   misses observed tonight.
b. **Engine:** `_claim_conversation_index` should not index conversations
   whose first turn is itself a recall probe, and/or index content should
   carry a conversation-start discriminator so multiple "first thing" rows
   can't collapse into one dominant stale answer. Engine/memory track —
   HU-2472/HU-2712 posture.
c. Not fixed by (a): grounded_wit (echo 0.16-0.20 vs corpus floor 0.23),
   engagement 3/6, identity 2/6 — voice-quality dims owned by the W6 chain.
   r10 was therefore NOT armed: re-running now re-fails those dims and burns
   the daily z.ai budget. Re-arm decision belongs to the HU-2712 gate owner
   once the quality fixes land.
