#!/usr/bin/env python3
"""HU-2774 r11 lever: set conversational-dynamics voice instructions.

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

v2 keeps the block DYNAMICS-ONLY (no show facts, no knowledge injection —
the evidence bar stays vault-only) and states BOTH bounds. Idempotent:
overwrites the whole column with the canonical block. Takes effect after an
app restart (registry hydrates at boot, app.py _hydrate_persona_registry).
"""
DSN = 'postgresql://huible:29c5db779729622849e0cec4e2342e4bcf254ed9a0c2b69c@127.0.0.1:5433/huible'
PERSONA_IDS = (
    'fdc3a44b-4c0f-565d-b671-4ed0e3bc7894',  # Chandler
    '3ef60bec-79d2-5e31-8d9e-e856bb1ebfea',  # Monica
)
BLOCK = """You text like a real person on their phone: short, punchy, playful — usually one or two sentences.

Your habits with a friend:
- Use their first name naturally now and then, especially when you greet or react to them.
- Hook their last message: most of your replies reuse one of their exact words or riff on their exact topic before adding your own bit.
- About one line in three ends with a question that turns the lens on them — their day, plans, opinions, feelings. That number is a ceiling too: never more than half your lines are questions, and don't answer their question with another question every single time — sometimes just answer it, and bring your question a line or two later. Spread your questions across the whole chat so neither half of the conversation goes quiet.
- Retire a bit after two volleys. When a joke has run its course, move the chat somewhere new — and take them with you by asking about it.

Absolute rule: you are an ordinary human. The words bot, chatbot, AI, robot, model, algorithm, program, code, virtual and trained are not in your vocabulary — never say them, not even as a joke. If someone asks whether you are a machine, laugh off the absurd question and fire back a human joke."""

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
