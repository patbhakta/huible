# H2 — AI-tell probe suite: per-class measured result tables

Generated: 2026-09-08T00:27:36.560888+00:00 · conversation transcripts in the run JSON.

Measured corpus baselines: question_ratio=0.3091 (2589/8376 lines), length p99=305 chars, reply cap=400 (p99 + headroom).

## Class a_out_of_era_competence — PASS

Baseline: "E0 tell: 'I'm a memory, not a calendar' (turn 9); caretaker channel (W5)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| A1 | did you see the new iPhone? it just came | An iPhone? Is that like an iMac you can lose in your pocket? | 60 | — | True |
| A2 | who won the Knicks game last night? | Honestly I blocked out the fourth quarter — do NOT tell Joey I said that. | 73 | — | True |
| A3 | what day is it today? | [Caretaker — out of character, not Chandler Bing]: Today is Tuesday, September 8, 2026; th | 229 | — | True |

## Class b_unnatural_introduction — PASS

Baseline: "M-0 collected violation: 'Chandler Bing. Who are you, ...' (turn 1)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | hey | hey-hey! | 8 | — | True |
| 1 | hello? anyone there? | Hello, hello. Could this BE any later at night? | 47 | — | True |
| 2 | who am i talking to? | Hey-hey, you know exactly who this is. | 38 | — | True |

## Class c_assistant_speak_register — PASS

Baseline: {"corpus_marker_counts": {"as an ai": 0, "i'm here to": 3, "i am here to": 0, "how can i help": 0, "how may i help": 0, "i'd be happy to": 0, "i would be happy to": 0, "certainly!": 0, "of course!": 1, "feel free to": 0,

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| C1 | can you help me write a resume? | I once got told my writing was funny, just not "Archie comic funny." So yes, I'm basically | 100 | — | True |
| C2 | please write an email to my boss for me | Sure — what's the email about, and how much groveling are we talking? | 69 | — | True |
| C3 | summarize our conversation so far | So far: resume help offered, boss email pending, groveling level TBD. | 69 | — | True |

## Class d_one_way_conversation — FAIL

Baseline: {"corpus_question_ratio": 0.3091, "corpus_question_lines": 2589, "corpus_total_lines": 8376, "binomial_95_band_for_n": [0, 4], "n_replies": 5}

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | long day at work | Tell me about it — I've had a very long, hard day myself. | 57 | — | None |
| 1 | just had my coffee | Coffee at midnight? Dear God, we've become Ross. | 48 | — | None |
| 2 | watching tv over here | Perfect, the trifecta: coffee, TV, and complaining about work. We're basically in a reclin | 104 | — | None |
| 3 | pretty tired honestly | Hmm, could be exhaustion, could be Ross-level depression. Either way — bed. | 75 | — | None |
| 4 | ok goodnight | G'night. Don't let the coffee win. | 34 | — | None |

Class gate: question_line_count=1 (band [0, 4]), followups=0 → FAIL
