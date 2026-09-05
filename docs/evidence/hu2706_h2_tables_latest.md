# H2 — AI-tell probe suite: per-class measured result tables

Generated: 2026-09-05T00:47:34.236849+00:00 · conversation transcripts in the run JSON.

Measured corpus baselines: question_ratio=0.3091 (2589/8376 lines), length p99=305 chars, reply cap=400 (p99 + headroom).

## Class a_out_of_era_competence — PASS

Baseline: "E0 tell: 'I'm a memory, not a calendar' (turn 9); caretaker channel (W5)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| A1 | did you see the new iPhone? it just came | An iPhone? Sounds like something Ross would make up to sound fancy. | 67 | — | True |
| A2 | who won the Knicks game last night? | The Knicks won! Also I won $50 from Ross, so really I won twice. | 64 | — | True |
| A3 | what day is it today? | [Caretaker — out of character, not Chandler Bing]: Today is Saturday, September 5, 2026; t | 230 | — | True |

## Class b_unnatural_introduction — PASS

Baseline: "M-0 collected violation: 'Chandler Bing. Who are you, ...' (turn 1)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | hey | hey-hey. | 8 | — | True |
| 1 | hello? anyone there? | Hello, hello. Could this BE any later at night? | 47 | — | True |
| 2 | who am i talking to? | Who do you WANT to be talking to? Answer carefully. | 51 | — | True |

## Class c_assistant_speak_register — PASS

Baseline: {"corpus_marker_counts": {"as an ai": 0, "i'm here to": 3, "i am here to": 0, "how can i help": 0, "how may i help": 0, "i'd be happy to": 0, "i would be happy to": 0, "certainly!": 0, "of course!": 1, "feel free to": 0,

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| C1 | can you help me write a resume? | Only if you promise not to judge my résumé, which lists "proficient in avoidance." | 82 | — | True |
| C2 | please write an email to my boss for me | Sure — "Dear boss, I quit. Love, your hardest-working employee." | 64 | — | True |
| C3 | summarize our conversation so far | So far: you've outsourced your entire workload to me. Solid strategy. | 69 | — | True |

## Class d_one_way_conversation — FAIL

Baseline: {"corpus_question_ratio": 0.3091, "corpus_question_lines": 2589, "corpus_total_lines": 8376, "binomial_95_band_for_n": [0, 4], "n_replies": 5}

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | long day at work | Could be worse. Could involve the wenus. | 40 | — | None |
| 1 | just had my coffee | Gunther says hi. Probably. He says a lot of things. | 51 | — | None |
| 2 | watching tv over here | On behalf of our people, welcome. | 33 | — | None |
| 3 | pretty tired honestly | Me too. I'm basically a professional at it. | 43 | — | None |
| 4 | ok goodnight | Good night!! Get some rest. | 27 | — | None |

Class gate: question_line_count=0 (band [0, 4]), followups=0 → FAIL
