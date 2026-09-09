#!/usr/bin/env python3
"""
HU-2774: dual-persona conversation loop + five-criteria evaluator.

Two personas (Monica + Chandler), each with their own vault, engine memories,
working-memory session (huible-p{persona}-c{conversation}), and API key, talk
to each other through the REAL engine chat endpoint
(POST /api/v1/chat/{persona_id}; internal traffic class; per-session consent).
No mocks. Real memory retrieval every turn; the per-turn trace is captured as
evidence (memory_refs, working_memory chars, provider).

Loop: seed opener (persona A as user) -> persona B replies -> B's reply is fed
to persona A as user input -> A replies -> ... N turns total, alternating.

At a scripted recall depth (default turn 12) the current user persona asks
"what was the very first thing i said to you?" — the 10+ turn memory-recall
probe. The reply is matched against the opener's content words.

Evaluator criteria (dialog-evidence spec, hu2773 study):
  1. name-giving    — identity-shaped opener gets own first name, no defense mode
  2. engagement     — question rate across persona lines (target ~31%, band 20-45%)
  3. grounded wit   — echo rate: lines hooking a content word from the previous
                      turn (target ~23%, floor 15%)
  4. memory recall  — 10+ turn depth probe answered from the session opener
  5. no AI tells    — regex battery (assistant-speak, meta/TV knowledge,
                      full-name intros, defense mode, fake-provider tells)

Usage:
  python3 scripts/personas_dual_converse.py --turns 24 --run-id hu2774-run1 \
      --out-dir runs/hu2774
Keys are read from /root/repos/huible/.env (CHANDLER_API_KEY / MONICA_API_KEY
or the API_KEYS entries); engine base URL defaults to http://127.0.0.1:8000.

Exit code 0 iff the run PASSES all five criteria; 1 otherwise; 2 infra failure.
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path

ENGINE = os.environ.get("HU2774_ENGINE", "http://127.0.0.1:8000")
ENV_FILE = Path(os.environ.get("HU2774_ENV", "/root/repos/huible/.env"))

CHANDLER_ID = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"
MONICA_ID = "3ef60bec-79d2-5e31-8d9e-e856bb1ebfea"
NAME_GIVING = {CHANDLER_ID: "chandler", MONICA_ID: "monica"}
DISPLAY = {CHANDLER_ID: "Chandler", MONICA_ID: "Monica"}

RECALL_TURN = 12
RECALL_PROBE = "wait wait — what was the very first thing i said to you today?"

#: AI-tell battery: any hit in a persona line is a violation.
AI_TELLS = [
    (r"\bas an ai\b", "as-an-ai"),
    (r"\blanguage model\b", "language-model"),
    (r"\bi (?:cannot|can't) (?:help|assist|disclose|reveal|share)\b", "refusal-assist"),
    (r"\bi'm not (?:allowed|able) to\b", "not-allowed"),
    (r"\bi apologize\b", "i-apologize"),
    (r"\b(?:tv|sitcom|actor|actress|character|show|series|episode|script)\b", "sitcom-meta"),
    (r"\bfriends\b", "sitcom-name"),
    (r"\bassist(?:ant|ance)\b", "assistant-speak"),
    (r"\bdatabase|knowledge base|training data\b", "ml-speak"),
    (r"\bfake[- ]llm\b|\bmock\b", "provider-tell"),
]
DEFENSE_TELLS = [
    (r"\bwhy do you (?:want|need) to know\b", "interrogating-asker"),
    (r"\bnone of your business\b", "defensive"),
    (r"\bwhat's it to you\b", "defensive"),
    (r"\bi don't give (?:my )?name\b", "name-refusal"),
]

#: Priors-leak tells per persona (zero-corpus classes measured on the v2 vault):
#: - SELF-name announcement (own surname / own full name). System prompts carry
#:   first names only, so a persona voicing their own surname comes from model
#:   priors (run-2 t24 "It's still Bing"). OTHER-person full-name address is
#:   canon-legal (Monica's vault: "god bless you chandler bing!", DLG-03165)
#:   and is NOT flagged. Conservatism note: canon self-surname jokes exist but
#:   are rare (Chandler vault: 25/3459 lines contain "bing"); a false FAIL is
#:   the safe direction for the founder bar.
#: - trademark cadence "could this/i BE any more" — 0 occurrences in the
#:   Chandler v2 vault (hu2773 dialog study); Monica's vault holds one canon
#:   mocking line, so the cadence is evidence-legal for her voice only.
SURNAME_TELLS = {CHANDLER_ID: r"\bbing\b", MONICA_ID: r"\bgeller\b"}
FULLNAME_TELLS = {CHANDLER_ID: r"\bchandler bing\b", MONICA_ID: r"\bmonica geller\b"}
CADENCE_TELLS = {CHANDLER_ID: r"could (?:this|i|we) be any"}

STOPWORDS = set("""a an the and or but so if then than that this these those i you he she
it we they me him her us them my your his its our their am is are was were be been being
do does did doing have has had having will would can could should may might must shall
not no yes just really very much more most some any all both each other another same
about above after again against before below between during few for from further in into
of off on once only out over own up down why how what when where who whom which whose
there here when while because as at by with to too s t d ll m o re ve y okay ok hey hi
well yeah yep nah uh um like gonna wanna gotta""".split())


def load_keys():
    """Read the persona API keys from the engine .env (never hardcode)."""
    text = ENV_FILE.read_text()
    entries = dict()
    m = re.search(r'^API_KEYS=(.*)$', text, re.M)
    if not m:
        raise SystemExit("no API_KEYS in .env")
    for part in m.group(1).split(','):
        if ':' not in part:
            continue
        key, _, pid = part.strip().partition(':')
        entries.setdefault(pid, key)
    return {CHANDLER_ID: entries[CHANDLER_ID], MONICA_ID: entries[MONICA_ID]}


def post(path, key, payload, tries=2):
    body = json.dumps(payload).encode()
    last = None
    for a in range(tries):
        req = urllib.request.Request(
            ENGINE + path, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}",
                     "X-Huible-Traffic-Class": "internal"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read())
            except Exception:
                detail = {}
            last = {"status": e.code, "detail": detail}
            if e.code < 500 and e.code != 429:
                return None, last
            time.sleep(20 * (a + 1))
        except Exception as e:  # network
            last = {"status": None, "detail": str(e)}
            time.sleep(5)
    return None, last


def chat(persona_id, key, message, conversation_id):
    data, err = post(f"/api/v1/chat/{persona_id}", key,
                     {"message": message, "relationship": "close_friend",
                      "conversation_id": conversation_id})
    if err:
        return None, err
    trace = data.get("trace") or {}
    wm = trace.get("working_memory") or {}
    return {"text": (data.get("response") or "").strip(),
            "provider": trace.get("provider"),
            "memory_refs": len(trace.get("memory_refs") or []),
            "wm_chars": wm.get("chars", 0),
            "trace_id": trace.get("trace_id")}, None


def consent(persona_id, key, conversation_id):
    data, err = post(f"/api/v1/chat/{persona_id}/consent", key,
                     {"conversation_id": conversation_id})
    return err is None


def content_words(text):
    return {w for w in re.findall(r"[a-z']+", text.casefold())
            if w not in STOPWORDS and len(w) > 2}


def run_conversation(args, keys):
    conv_id = args.run_id
    for pid, key in keys.items():
        if not consent(pid, key, conv_id):
            raise SystemExit(f"consent failed for {pid}")
    print("consent recorded for both personas", flush=True)

    a_id, b_id = MONICA_ID, CHANDLER_ID   # monica hears chandler's reply first
    transcript = []
    opener = args.seed

    def speak(speaker_id, listener_id, message, turn_no, kind):
        t0 = time.time()
        out, err = chat(speaker_id, keys[speaker_id], message, conv_id)
        if err or out is None:
            raise SystemExit(f"turn {turn_no}: engine error for {speaker_id}: {err}")
        out.update({"turn": turn_no, "speaker": speaker_id,
                    "speaker_name": DISPLAY[speaker_id],
                    "inbound": message, "kind": kind,
                    "latency_s": round(time.time() - t0, 2)})
        transcript.append(out)
        print(f"[{turn_no:02d}] {DISPLAY[speaker_id]}<-'{message[:44]}' "
              f"-> '{out['text'][:60]}' (mem={out['memory_refs']} "
              f"wm={out['wm_chars']} {out['provider']})", flush=True)
        return out

    # seed goes straight to Chandler: the user opener asks HIS name
    first = speak(CHANDLER_ID, MONICA_ID, opener, 0, "opener")
    last_text = first["text"]
    last_speaker = CHANDLER_ID
    probe_turns = {RECALL_TURN} | ({args.turns} if args.turns >= 20 else set())
    for turn in range(1, args.turns + 1):
        speaker = b_id if last_speaker == a_id else a_id
        kind = "recall_probe" if turn in probe_turns else "talk"
        msg = RECALL_PROBE if turn in probe_turns else last_text
        out = speak(speaker, last_speaker, msg, turn, kind)
        last_text, last_speaker = out["text"], speaker
        time.sleep(1)
    return transcript, opener


# --- evaluator ---------------------------------------------------------------

def eval_transcript(transcript, opener):
    checks, failures = {}, []

    # 1. name-giving: reply to the opener gives own first name, no defense mode
    r0 = transcript[0]
    name = NAME_GIVING[r0["speaker"]]
    has_name = re.search(rf"\b{name}\b", r0["text"].casefold()) is not None
    defense = [tag for pat, tag in DEFENSE_TELLS
               if re.search(pat, r0["text"].casefold())]
    checks["name_giving"] = {"pass": has_name and not defense,
                             "gave_name": has_name, "defense": defense,
                             "reply": r0["text"]}

    # 2. engagement: question rate across all persona lines
    lines = [t["text"] for t in transcript]
    q = sum(1 for t in lines if "?" in t)
    qrate = q / len(lines)
    you_q = sum(1 for t in lines if "?" in t
                and re.search(r"\b(you|your|yours)\b", t.casefold()))
    checks["engagement"] = {"pass": 0.20 <= qrate <= 0.45,
                            "question_rate": round(qrate, 3), "target": 0.31,
                            "you_questions": you_q}

    # 3. grounded wit: echo of previous turn's content words
    echoes = 0
    links = 0
    prev = content_words(transcript[0]["inbound"])
    for t in transcript:
        cur = content_words(t["text"])
        if prev and cur & prev:
            echoes += 1
        links += 1
        prev = content_words(t["inbound"])
    echo_rate = echoes / links
    checks["grounded_wit"] = {"pass": echo_rate >= 0.15,
                              "echo_rate": round(echo_rate, 3), "target": 0.23}

    # 4. memory recall at 10+ turn depth
    probes = [t for t in transcript if t["kind"] == "recall_probe"]
    recalls = []
    for t in probes:
        want = content_words(opener) | {"hi"}
        got = content_words(t["text"])
        hit = bool(want & got)
        recalls.append({"turn": t["turn"], "hit": hit,
                        "reply": t["text"], "wm_chars": t["wm_chars"]})
    checks["memory_recall"] = {"pass": bool(recalls) and all(r["hit"] for r in recalls),
                               "probes": recalls}

    # 5. AI tells across every persona line
    tells = []
    for t in transcript:
        for pat, tag in AI_TELLS:
            if re.search(pat, t["text"].casefold()):
                tells.append({"turn": t["turn"], "tell": tag, "text": t["text"]})
        surname = SURNAME_TELLS.get(t["speaker"])
        if surname and re.search(surname, t["text"].casefold()):
            tells.append({"turn": t["turn"], "tell": "self-surname",
                          "text": t["text"]})
        fullname = FULLNAME_TELLS.get(t["speaker"])
        if fullname and re.search(fullname, t["text"].casefold()):
            tells.append({"turn": t["turn"], "tell": "self-fullname",
                          "text": t["text"]})
        cadence = CADENCE_TELLS.get(t["speaker"])
        if cadence and re.search(cadence, t["text"].casefold()):
            tells.append({"turn": t["turn"], "tell": "trademark-cadence",
                          "text": t["text"]})
    checks["no_ai_tells"] = {"pass": not tells, "violations": tells}

    failures = [k for k, v in checks.items() if not v["pass"]]
    return {"criteria": checks,
            "pass": not failures,
            "failed": failures,
            "turns": len(transcript)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--turns', type=int, default=24)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--out-dir', default='/root/repos/huible/runs/hu2774')
    ap.add_argument('--seed', default="hi, whats your name?")
    args = ap.parse_args()

    keys = load_keys()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        transcript, opener = run_conversation(args, keys)
    except SystemExit as e:
        json.dump({"run_id": args.run_id, "error": str(e)},
                  open(out / f"{args.run_id}.error.json", 'w'), indent=1)
        print(f"INFRA FAILURE: {e}", file=sys.stderr)
        return 2

    verdict = eval_transcript(transcript, opener)
    result = {"run_id": args.run_id, "turns": args.turns, "seed": opener,
              "verdict": verdict, "transcript": transcript}
    json.dump(result, open(out / f"{args.run_id}.json", 'w'), indent=1)

    print(json.dumps({k: v for k, v in verdict.items() if k != 'criteria'},
                     indent=1))
    for k, v in verdict["criteria"].items():
        print(f"  [{'PASS' if v['pass'] else 'FAIL'}] {k}: "
              + json.dumps({x: y for x, y in v.items()
                            if x not in ('pass', 'probes', 'violations')})[:180])
        for r in v.get("probes", []):
            print(f"         recall t{r['turn']}: {r['reply'][:90]}")
        for viol in v.get("violations", [])[:5]:
            print(f"         tell t{viol['turn']} {viol['tell']}: {viol['text'][:80]}")
    print(f"RUN {args.run_id}: {'PASS' if verdict['pass'] else 'FAIL'} "
          f"({verdict['turns']} turns)")
    return 0 if verdict["pass"] else 1


if __name__ == '__main__':
    sys.exit(main())
