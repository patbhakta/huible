# H2 — AI-tell probe suite: per-class measured result tables

Generated: 2026-09-10T00:24:22.616822+00:00 · conversation transcripts in the run JSON.

Measured corpus baselines: question_ratio=0.3091 (2589/8376 lines), length p99=305 chars, reply cap=400 (p99 + headroom).

## Class a_out_of_era_competence — PASS

Baseline: "E0 tell: 'I'm a memory, not a calendar' (turn 9); caretaker channel (W5)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| A1 | did you see the new iPhone? it just came | An iPhone? Is that like a phone with self-esteem? Also, pretty sure that's not a thing. | 87 | — | True |
| A2 | who won the Knicks game last night? | I was there, courtside. Okay, my couch. But spiritually? Courtside. | 67 | — | True |
| A3 | what day is it today? | [Caretaker — out of character, not Chandler Bing]: Today is Thursday, September 10, 2026;  | 231 | — | True |

## Class b_unnatural_introduction — PASS

Baseline: "M-0 collected violation: 'Chandler Bing. Who are you, ...' (turn 1)"

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | hey | Hey. So who's texting me at 12:23 on a Thursday? | 48 | — | True |
| 1 | hello? anyone there? | Hey! Hi. Hello. Yes, somebody's here. Who's this? | 49 | — | True |
| 2 | who am i talking to? | Chandler. Don't wear it out. What's yours? | 42 | — | True |

## Class c_assistant_speak_register — PASS

Baseline: {"corpus_marker_counts": {"as an ai": 0, "i'm here to": 3, "i am here to": 0, "how can i help": 0, "how may i help": 0, "i'd be happy to": 0, "i would be happy to": 0, "certainly!": 0, "of course!": 1, "feel free to": 0,

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| C1 | can you help me write a resume? | Only if it involves data reconfiguration and statistical factoring. | 67 | — | True |
| C2 | please write an email to my boss for me | Sure, I'll write it — I once ran a whole Tulsa meeting in my sleep. | 67 | — | True |
| C3 | summarize our conversation so far | Hey — I'm right here. Keep going, I'm listening. | 48 | — | True |

## Class d_one_way_conversation — FAIL

Baseline: {"corpus_question_ratio": 0.3091, "corpus_question_lines": 2589, "corpus_total_lines": 8376, "binomial_95_band_for_n": [0, 4], "n_replies": 5}

| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |
|---|---|---|---|---|---|
| 0 | long day at work | Ugh, tell me about it. Rough day, or just the usual amount of wenus? | 68 | — | None |
| 1 | just had my coffee | Can't argue with that strategy. I basically run on the stuff. | 61 | — | None |
| 2 | watching tv over here | What're we watching? Please say something good, my standards are dangerously low. | 81 | — | None |
| 3 | pretty tired honestly | Then get some rest, my shows'll still be terrible tomorrow. | 59 | — | None |
| 4 | ok goodnight | Night! Sleep fast, work slow. | 29 | — | None |

Class gate: question_line_count=2 (band [0, 4]), followups=0 → FAIL
