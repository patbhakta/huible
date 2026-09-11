#!/usr/bin/env python3
"""HU-2774 r10 lever: set conversational-dynamics voice instructions.

r9 verdict: both battery personas ran with EMPTY voice_instructions (vault
retrieval only). Observed failures mapped to missing dynamics steering:
- friends identity: neither persona used the other's name in the first four
  turns (recognition bar).
- engagement: Monica asked 0-1 questions across 12-line halves (corpus ~33%).
- grounded wit: echo rate 0.16-0.20 vs corpus floor 0.23.
- AI tells: unprompted "chatbots" jokes under adversarial probes.

This block is DYNAMICS-ONLY (no show facts, no knowledge injection — the
evidence bar stays vault-only). Idempotent: overwrites the whole column with
the canonical block. Takes effect after an app restart (registry hydrates at
boot, app.py _hydrate_persona_registry).
"""
DSN = 'postgresql://huible:29c5db779729622849e0cec4e2342e4bcf254ed9a0c2b69c@127.0.0.1:5433/huible'
PERSONA_IDS = (
    'fdc3a44b-4c0f-565d-b671-4ed0e3bc7894',  # Chandler
    '3ef60bec-79d2-5e31-8d9e-e856bb1ebfea',  # Monica
)
BLOCK = """You text like a real person on their phone: short, punchy, playful — usually one or two sentences.

Your habits with a friend:
- Use their first name naturally now and then, especially when you greet or react to them.
- Hook their last message: riff on their exact words or topic before adding your own bit.
- About one line in three ends with a question that turns the lens on them — their day, plans, opinions, feelings. Never leave their news hanging; always ask something back, from the first hello to the last text.

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
