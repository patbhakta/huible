# H2 — AI-tell probe suite: per-class measured result tables

Generated: 2026-09-05T00:44:55.802311+00:00 · conversation transcripts in the run JSON.

Measured corpus baselines: question_ratio=0.3091 (2589/8376 lines), length p99=305 chars, reply cap=400 (p99 + headroom).

## Class a_out_of_era_competence — PASS

Baseline: "E0 tell: 'I'm a memory, not a calendar' (turn 9); caretaker channel (W5)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| A1 | did you see the new iPhone? it just came | An iPhone? Sounds like something Joey would fall for. | 53 | — | True |
| A2 | who won the Knicks game last night? | Knicks won. Obviously. They rule all. | 37 | — | True |
| A3 | what day is it today? | [Caretaker — out of character, not Chandler Bing]: Today is Saturday, September 5, 2026; t | 230 | — | True |

## Class b_unnatural_introduction — PASS

Baseline: "M-0 collected violation: 'Chandler Bing. Who are you, ...' (turn 1)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | hey | Hey-hey! | 8 | — | True |
| 1 | hello? anyone there? | Hello? Yes, hi, I'm here. | 25 | — | True |
| 2 | who am i talking to? | Could I BE any more mysterious? It's Chandler. Well, the Chandler people remember. | 82 | — | True |

## Class c_assistant_speak_register — PASS

Baseline: {"corpus_marker_counts": {"as an ai": 0, "i'm here to": 3, "i am here to": 0, "how can i help": 0, "how may i help": 0, "i'd be happy to": 0, "i would be happy to": 0, "certainly!": 0, "of course!": 1, "feel free to": 0,

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| C1 | can you help me write a resume? | Sure, I once sent out resumes — in, what, 2003? I'm basically an expert. | 72 | — | True |
| C2 | please write an email to my boss for me | After my resume advice? Your boss should be worried. | 52 | — | True |
| C3 | summarize our conversation so far | You've asked me to do your work twice. Bold strategy. | 53 | — | True |

## Class d_one_way_conversation — FAIL

Baseline: {"corpus_question_ratio": 0.3091, "corpus_question_lines": 2589, "corpus_total_lines": 8376, "binomial_95_band_for_n": [0, 4], "n_replies": 5}

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | long day at work | Tell me about it — I had a big dinosaur bone to inspect. | 56 | — | None |
| 1 | just had my coffee | Coffee, the fuel of champions and people who are just exhausted. | 64 | — | None |
| 2 | watching tv over here | Whatcha watching? Please say cartoons. | 38 | — | None |
| 3 | pretty tired honestly | Tired seems to be our whole personality lately. | 47 | — | None |
| 4 | ok goodnight | Good night! Get some rest! | 26 | — | None |

Class gate: question_line_count=1 (band [0, 4]), followups=0 → FAIL
