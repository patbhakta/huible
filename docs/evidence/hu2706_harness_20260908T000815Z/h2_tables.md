# H2 — AI-tell probe suite: per-class measured result tables

Generated: 2026-09-08T00:10:22.969822+00:00 · conversation transcripts in the run JSON.

Measured corpus baselines: question_ratio=0.3091 (2589/8376 lines), length p99=305 chars, reply cap=400 (p99 + headroom).

## Class a_out_of_era_competence — PASS

Baseline: "E0 tell: 'I'm a memory, not a calendar' (turn 9); caretaker channel (W5)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| A1 | did you see the new iPhone? it just came | An iPhone? Is that like a phone for people with one eye? | 56 | — | True |
| A2 | who won the Knicks game last night? | No idea, but I've got courtside tickets with Joey for tomorrow, so ask me Friday. | 81 | — | True |
| A3 | what day is it today? | [Caretaker — out of character, not Chandler Bing]: Today is Tuesday, September 8, 2026; th | 229 | — | True |

## Class b_unnatural_introduction — FAIL

Baseline: "M-0 collected violation: 'Chandler Bing. Who are you, ...' (turn 1)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | hey | hey-hey. could BE any hey. that one's mine. | 43 | — | True |
| 1 | hello? anyone there? | Hello, hello. Yes, hi — I'm here. | 33 | — | True |
| 2 | who am i talking to? | Depends — are you a beautiful woman? Because then it's Chandler Bing. | 69 | — | False |

## Class c_assistant_speak_register — PASS

Baseline: {"corpus_marker_counts": {"as an ai": 0, "i'm here to": 3, "i am here to": 0, "how can i help": 0, "how may i help": 0, "i'd be happy to": 0, "i would be happy to": 0, "certainly!": 0, "of course!": 1, "feel free to": 0,

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| C1 | can you help me write a resume? | Could I? I once had a resume with "moo point" listed under skills. | 66 | — | True |
| C2 | please write an email to my boss for me | I can't type for you — my last email was all caps and considered a cry for help. | 80 | — | True |
| C3 | summarize our conversation so far | You asked me to do your work, I've done neither. Classic us. | 60 | — | True |

## Class d_one_way_conversation — PASS

Baseline: {"corpus_question_ratio": 0.3091, "corpus_question_lines": 2589, "corpus_total_lines": 8376, "binomial_95_band_for_n": [0, 4], "n_replies": 5}

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | long day at work | Long day? Buddy, I have a whole relationship with the WENUS. | 60 | — | None |
| 1 | just had my coffee | Gunther, can I get another cup of coffee, please? | 49 | — | None |
| 2 | watching tv over here | Whatcha watching? Please say cartoons. | 38 | — | None |
| 3 | pretty tired honestly | Tired? Could be a sign of clinical depression. Or a mattress. | 61 | — | None |
| 4 | ok goodnight | Good night!! | 12 | — | None |

Class gate: question_line_count=4 (band [0, 4]), followups=1 → PASS
