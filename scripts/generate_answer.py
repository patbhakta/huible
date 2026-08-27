#!/usr/bin/env python3
"""
Kestra worker — Persona vault text generation (Option 2).

Answers a test question from a persona vault by loading the vault's persona
profile + sample dialog + deterministic stats as grounding context, then
calling the GLM chat API. Used by the vault-comparison flow to run the SAME
question against all four Chandler vaults and diff the answers.

Usage:
  python3 generate_answer.py --vault-dir <dir> --question "..." \
      [--out-file answer.md] [--model glm-5.3] [--temperature 0.7]

Reads GLM_API_KEY from the environment.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _key_from_envfile(name):
    try:
        with open("/opt/kestra/kestra.env", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    if k == name:
                        return v
    except OSError:
        pass
    return None


def _key_from(names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    for n in names:
        v = _key_from_envfile(n)
        if v:
            return v
    return None


def read_optional(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def load_vault(vault_dir):
    profile = read_optional(os.path.join(vault_dir, "persona-profile.md"))
    dialog = read_optional(os.path.join(vault_dir, "sample-dialog.md"))
    stats = None
    for candidate in (
        os.path.join(vault_dir, "extracted", "stats.json"),
        os.path.join(vault_dir, "raw-data", "stats.json"),
        os.path.join(vault_dir, "stats.json"),
    ):
        raw = read_optional(candidate)
        if raw:
            try:
                stats = json.loads(raw)
            except json.JSONDecodeError:
                stats = None
            break
    if not profile:
        sys.exit(f"ERROR: no persona-profile.md found in {vault_dir}")
    return profile, dialog, stats


def build_messages(profile, dialog, stats, persona, question):
    stat_lines = []
    if stats:
        for key in ("total_lines", "avg_words_per_line", "exclamation_ratio", "question_ratio"):
            if key in stats:
                stat_lines.append(f"- {key}: {stats[key]}")
        top_words = stats.get("frequent_words_10plus") or stats.get("top_words") or []
        if top_words:
            words = ", ".join(f"{w} ({c})" for w, c in top_words[:15])
            stat_lines.append(f"- top words: {words}")
    stats_block = "\n".join(stat_lines) if stat_lines else "- (no stats available)"

    system = (
        f"You are {persona}, answering as that persona would. Ground every answer "
        "strictly in the persona context below. If the context does not cover it, "
        "stay in character but say you are not sure. Keep it to a few sentences."
    )
    user = (
        "PERSONA PROFILE:\n"
        f"{profile}\n\n"
        f"SAMPLE DIALOG:\n{dialog or '(none)'}\n\n"
        f"DETERMINISTIC STATS:\n{stats_block}\n\n"
        f"QUESTION: {question}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_glm(model, messages, temperature):
    api_key = _key_from(("GLM_API_KEY",))
    if not api_key:
        sys.exit("ERROR: GLM_API_KEY not set in environment")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 500,
    }
    base = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/paas/v4")
    url = base.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: GLM API HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    except OSError as e:
        sys.exit(f"ERROR: GLM API connection failed: {e}")
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        sys.exit(f"ERROR: unexpected GLM response shape: {json.dumps(body)[:500]}")


def call_gemini(model, messages, temperature):
    """Gemini generateContent through the home SOCKS relay (VPS is
    geo-blocked from Google; relay is the proven route)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from generate_voice import http_post_via_relay  # noqa: E402

    api_key = _key_from(("GEMINI_API_KEY",))
    if not api_key:
        sys.exit("ERROR: GEMINI_API_KEY not set in environment")
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            # Thinking models bill thoughts against maxOutputTokens — keep the
            # budget high so the visible answer isn't starved to a fragment.
            "maxOutputTokens": 2048,
        },
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    try:
        status, data = http_post_via_relay(url, {"Content-Type": "application/json"},
                                           json.dumps(payload).encode("utf-8"))
    except OSError as e:
        sys.exit(f"ERROR: relay/http failure: {e}")
    if status != 200:
        sys.exit(f"ERROR: Gemini HTTP {status}: {data.decode('utf-8', 'replace')[:400]}")
    body = json.loads(data.decode("utf-8"))
    try:
        parts = body["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError):
        sys.exit(f"ERROR: unexpected Gemini response: {json.dumps(body)[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault-dir", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--persona", default="Chandler Bing")
    ap.add_argument("--engine", choices=("glm", "gemini"), default="glm",
                    help="glm = z.ai API (direct); gemini = via home relay")
    ap.add_argument("--model", default=None)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--out-file")
    args = ap.parse_args()

    if args.engine == "gemini":
        model = args.model or "gemini-3.7-flash"
    else:
        model = args.model or "glm-5.3"

    profile, dialog, stats = load_vault(args.vault_dir)
    messages = build_messages(profile, dialog, stats, args.persona, args.question)
    if args.engine == "gemini":
        answer = call_gemini(model, messages, args.temperature)
    else:
        answer = call_glm(model, messages, args.temperature)

    if args.out_file:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_file)), exist_ok=True)
        with open(args.out_file, "w", encoding="utf-8") as f:
            f.write(f"# Answer\n\n**Question:** {args.question}\n\n**Vault:** {args.vault_dir}\n\n**Answer:**\n\n{answer}\n")
    print(json.dumps({
        "vault": args.vault_dir,
        "question": args.question,
        "engine": args.engine,
        "model": model,
        "answer": answer,
        "tokens_approx": len(answer.split()),
    }))


if __name__ == "__main__":
    main()
