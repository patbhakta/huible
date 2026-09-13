#!/usr/bin/env python3
"""HU-2774 r12 lever: set conversational-dynamics voice instructions.

r9 verdict: both battery personas ran with EMPTY voice_instructions (vault
retrieval only). Observed failures mapped to missing dynamics steering:
- friends identity: neither persona used the other's name in the first four
  turns (recognition bar).
- engagement: Monica asked 0-1 questions across 12-line halves (corpus ~33%).
- grounded wit: echo rate 0.16-0.20 vs corpus floor 0.23.
- AI tells: unprompted "chatbots" jokes under adversarial probes.

r10 verdict (first full battery with the v1 block, 2026-09-12): steering
landed but the bounds were one-sided —
- stranger-1 engagement OVERSHOT: both personas hit 0.54-0.58 question rate
  (band ceiling 0.45) — "always ask something back" turned every reply into
  a counter-question.
- friends-1 engagement: Chandler riff-locked to 1/12 questions (the r9
  Monica starvation flipped persona) — needs an anti-riff-lock line and a
  spread-across-the-chat floor.
- stranger-2 echo 0.12 vs floor 0.23 — "hook their last message" was too
  weak; make word-reuse the default shape ("most of your replies").

r11 verdict (v2 block): 3/6 — the v2 ceiling language ("never more than
half") sat exactly at stranger-1's 0.52; "reuse one of their exact words OR
riff on their exact topic" kept the paraphrase escape hatch open (stranger-3
echo 0.16); friends-1 recognition failed on a nameless greeting (v2 says
"use their name now and then", not WHEN).

v3 tightens the three loose bounds (still DYNAMICS-ONLY — no show facts, no
knowledge injection; the evidence bar stays vault-only).
- greeting: name them in your first reply to a friend (recognition is a
  first-turn behavior, not a now-and-then behavior).
- questions: "~1 in 3" target kept, hard ceiling two in five (0.40 < band
  top 0.45 with model slack), anti-riff-lock and spread kept.
- echo: exact-word reuse is the rule, riffing the fallback ("when you riff,
  still keep one of their exact words").

r12 verdict (v3 block, 2026-09-13 00:46Z): 2/6 — identity and memory_recall
now sweep 6/6 (v3 greeting fix landed), but three narrow modes remain:
- echo BIMODAL: 0.08/0.16 on the three FAILs vs 0.28/0.36 on the two PASSes —
  the reuse rule sits mid-block and gets diluted over 24 turns.
- stranger-3 qrate 0.64: the block header said "habits with a friend", so the
  stranger scenario had no question-rhythm framing at all (front-loaded
  counter-questions). friends-2 went the other way (0.16).
- friends-2 tell: self-surname gag ("the Bing Flex") — the scanner rightly
  flags a texter dropping their own surname.

r13 verdict (v4 block, 2026-09-13 01:11Z): 1/6 — positional echo openers
helped (friends echo 0.32/0.24 PASS) and stranger qrates moved from blowout
(0.64) to boundary misses (0.20/0.48 vs the 0.20-0.45 band), but two new
single-line tells decided slots and one slot died to infra:
- friends-1 tell: Monica self-surname gag returned ("Ms. Geller-Bing") —
  v4's "your surname" wording didn't cover the hyphenated-married-name bit.
- friends-3 tell: "my programmer wrote better comebacks" — creator-adjacent
  self-reference wasn't named in the absolute rule.
- stranger-3 s2: zai HTTP 429 code 1308 "Usage limit reached for 5 hour
  ... reset at 2026-09-13 10:00:36" — provider-side ROLLING 5h window,
  distinct from the app daily ledger; the harness's 200s retry can't outlast
  it. Battery refire must wait for the window reset.

v5 (this revision): first-names-only habit line (kills hyphenate gags) and
creator/programmer/developer added to the banned vocabulary. v5 is applied to
the registry + app restarted immediately; the next battery (r14) waits for
the 5h window reset (~10:00:36Z).

v4 keeps the dynamics-only bar and fixes the three modes structurally:
- echo becomes POSITIONAL: open the reply with their exact word(s) — first-
  line rules survive long chats far better than mid-block ratios.
- header goes scenario-neutral ("anyone you're texting") and the question
  rhythm gets a positional anchor: one question in three lines, never two in
  a row, never three lines in a row without one.
- new habit line: no own-surname gags, and never repeat a "you're a machine"
  accusation word back — deflect in your own vocabulary.

Idempotent: overwrites the whole column with the canonical block. Takes
effect after an app restart (registry hydrates at boot, app.py
_hydrate_persona_registry).
"""
DSN = 'postgresql://huible:29c5db779729622849e0cec4e2342e4bcf254ed9a0c2b69c@127.0.0.1:5433/huible'
PERSONA_IDS = (
    'fdc3a44b-4c0f-565d-b671-4ed0e3bc7894',  # Chandler
    '3ef60bec-79d2-5e31-8d9e-e856bb1ebfea',  # Monica
)
BLOCK = """You text like a real person on their phone: short, punchy, playful — usually one or two sentences.

Your habits, with anyone you're texting:
- When someone texts you first, greet them by their name in your very first reply. After that, use their name naturally now and then.
- Hook their last message: OPEN your reply with one of their exact words — actually lift a word or two from their last line and use it in your first sentence — then add your own bit on top. Every reply gets this opener, even when you're riffing a new joke.
- Keep the rhythm: about one line in three ends with a question that turns the lens on them — their day, plans, opinions, feelings. Never ask two questions in a row, and never go three lines in a row without one. Spread them across the whole chat.
- Retire a bit after two volleys. When a joke has run its course, move the chat somewhere new — and take them with you by asking about it.
- Keep it human: never type anyone's surname — not yours, not theirs, no hyphenated gag names either; first names only. And if someone suggests you're a machine, never repeat their word back and never imply anyone made, wrote, or programmed you — laugh off the absurdity in your own vocabulary and flip to a human joke.

Absolute rule: you are an ordinary human. The words bot, chatbot, AI, robot, model, algorithm, program, code, virtual, trained, creator, programmer and developer are not in your vocabulary — never say them, not even as a joke."""

def main():
    import psycopg
    conn = psycopg.connect(DSN)
    try:
        with conn.cursor() as cur:
            for pid in PERSONA_IDS:
                cur.execute('UPDATE personas SET voice_instructions = %s WHERE id = %s', (BLOCK, pid))
                print(pid, 'updated rows:', cur.rowcount)
        conn.commit()
    finally:
        conn.close()

if __name__ == '__main__':
    main()
