#!/usr/bin/env python3
"""HU-2772: Chandler test portal — human test-subject eval surface (web).

Founder-directed (2026-09-09): after the HU-2771 chat eval passed, human test
subjects evaluate Chandler through THIS web portal. Channels (WhatsApp, SMS,
iMessage, ...) attach only at client onboarding — this surface is the pre-
channel validation gate (input 4 on the HU-2712 Chandler gate lift path).

Design constraints:
  * The persona-scoped bearer key NEVER reaches the browser. The browser talks
    only to this proxy; the proxy talks to the engine using the Chandler key
    resolved from API_KEYS / CHANDLER_API_KEY in the env file (same resolution
    order as scripts/personas_dual_converse.py).
  * Invite passcode gate (PORTAL_INVITE_TOKEN): outside test subjects enter
    only on Pat explicit go, so a session cannot start without the code that
    Pat shares with each invited subject. If unset at boot, a random one is
    generated and printed to the operator.
  * Traffic class "internal" (same as the dual-persona harness): this is an
    internal eval surface pre-launch; flipping the real-user ramp gate
    (PERSONA_CHAT_REAL_USER_TRAFFIC) is a founder/gate lever, not this script's.
  * G6 reality-framing consent card is surfaced verbatim from the engine's 409
    CONSENT_REQUIRED body and acknowledged through the sanctioned
    POST /api/v1/chat/{persona_id}/consent path before any persona reply.
  * Transcripts land in runs/hu2772/sessions/<session>.json (subject name,
    turns, provider, latency, feedback) — the evidence trail the founder
    human-eval step reads. Keep this directory out of git.

Usage:
    python3 scripts/chandler_portal.py
Env:
    HU2772_ENGINE       engine base URL (default http://127.0.0.1:8000)
    HU2772_ENV          env file holding API_KEYS / CHANDLER_API_KEY
    PORTAL_HOST         bind host (default 127.0.0.1)
    PORTAL_PORT         bind port (default 8765)
    PORTAL_INVITE_TOKEN invite passcode; generated + printed when unset
"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENGINE = "http://127.0.0.1:8000"
ENV_FILE = Path("/root/repos/huible/.env")
CHANDLER_ID = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"
TRAFFIC_CLASS = "internal"
RELATIONSHIP = "close_friend"
TRANSCRIPT_DIR = Path("runs/hu2772/sessions")
MAX_MESSAGE_CHARS = 2000
MAX_TURNS_PER_SESSION = 200
SESSION_TTL_S = 24 * 3600

_lock = threading.Lock()
_sessions: dict[str, dict] = {}


# --- env / key resolution (mirrors personas_dual_converse.py) ---------------


def load_env_entries(env_path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not env_path.exists():
        return entries
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip().strip('"').strip("'")
    return entries


def resolve_chandler_key(env_path: Path) -> str:
    entries = load_env_entries(env_path)
    direct = entries.get("CHANDLER_API_KEY", "").strip()
    if direct:
        return direct
    for entry in entries.get("API_KEYS", "").split(","):
        if ":" not in entry:
            continue
        key, _, persona = entry.strip().partition(":")
        if persona.strip().lower() == CHANDLER_ID:
            return key.strip()
    raise SystemExit(
        "no Chandler key found: set CHANDLER_API_KEY or an API_KEYS entry "
        f"key:{CHANDLER_ID} in {env_path}"
    )


# --- engine client -----------------------------------------------------------


def engine_post(
    base: str, path: str, key: str, payload: dict, tries: int = 2
) -> tuple[dict | None, dict | None]:
    body = json.dumps(payload).encode()
    last: dict | None = None
    for attempt in range(tries):
        req = urllib.request.Request(
            base + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "X-Huible-Traffic-Class": TRAFFIC_CLASS,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read()), None
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read())
            except Exception:
                detail = {}
            last = {"status": exc.code, "detail": detail}
            if exc.code < 500 and exc.code != 429:
                return None, last
            time.sleep(2 * (attempt + 1))
        except Exception as exc:  # network
            last = {"status": None, "detail": str(exc)}
            time.sleep(2)
    return None, last


# --- transcript store --------------------------------------------------------


def _transcript_path(session_id: str) -> Path:
    return TRANSCRIPT_DIR / f"{session_id}.json"


def _write_transcript(session: dict) -> None:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = _transcript_path(session["id"])
    path.write_text(json.dumps(session["transcript"], indent=2) + "\n")


def append_turn(session: dict, role: str, text: str, **extra) -> None:
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role": role, "text": text}
    record.update(extra)
    session["transcript"]["turns"].append(record)
    _write_transcript(session)


def prune_sessions() -> None:
    now = time.time()
    stale = [sid for sid, s in _sessions.items() if now - s["created"] > SESSION_TTL_S]
    for sid in stale:
        _sessions.pop(sid, None)


# --- portal logic ------------------------------------------------------------


def create_session(name: str) -> dict:
    sid = secrets.token_hex(8)
    session = {
        "id": sid,
        "name": name,
        "created": time.time(),
        "consent_acknowledged": False,
        "transcript": {
            "session_id": sid,
            "conversation_id": f"hu2772-portal-{sid}",
            "subject": name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "surface": "web-portal",
            "turns": [],
            "feedback": None,
        },
    }
    _sessions[sid] = session
    _write_transcript(session)
    return session


def handle_turn(session: dict, message: str, key: str, base: str) -> tuple[dict, int]:
    if len(session["transcript"]["turns"]) >= MAX_TURNS_PER_SESSION * 2:
        return {"ok": False, "error": "This session is full. Start a new one."}, 429

    append_turn(session, "user", message)
    t0 = time.perf_counter()
    data, err = engine_post(
        base,
        f"/api/v1/chat/{CHANDLER_ID}",
        key,
        {
            "message": message,
            "relationship": RELATIONSHIP,
            "conversation_id": session["transcript"]["conversation_id"],
            "user_name": session["name"],
        },
    )
    latency = round(time.perf_counter() - t0, 3)

    if err is None:
        text = (data.get("response") or "").strip() if data else ""
        trace = (data or {}).get("trace") or {}
        append_turn(
            session,
            "chandler",
            text,
            provider=trace.get("provider"),
            latency_s=latency,
        )
        return {"ok": True, "text": text}, 200

    status = err.get("status")
    detail = err.get("detail") or {}
    if status == 409:
        error = detail.get("error") or {}
        card = error.get("consent_card") or {}
        if card:
            return {"ok": True, "consentRequired": True, "card": card}, 200
        return {"ok": False, "error": "Engine requires consent (no card supplied)."}, 502
    if status in (401, 403):
        return {"ok": False, "error": "Engine auth failed — operator action needed."}, 502
    if status == 429:
        return {"ok": False, "error": "Engine is rate limited — try again in a minute."}, 429
    if status == 503:
        return {"ok": False, "error": "Chat is temporarily disabled."}, 503
    return {"ok": False, "error": "Engine unavailable — try again shortly."}, 502


# --- HTTP surface ------------------------------------------------------------


class PortalHandler(BaseHTTPRequestHandler):
    server_version = "ChandlerPortal/1.0"
    invite_token: str = ""
    chandler_key: str = ""
    engine_base: str = ENGINE

    def log_message(self, fmt: str, *args) -> None:  # quiet, one-line JSON-ish
        print(f"portal {self.address_string()} {fmt % args}", flush=True)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 65536:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _session(self, sid: str) -> dict | None:
        if not sid:
            return None
        with _lock:
            prune_sessions()
            return _sessions.get(sid)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/portal/healthz":
            self._send_json({"ok": True})
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:
        body = self._read_json()
        if self.path == "/portal/api/session":
            self._api_session(body)
        elif self.path == "/portal/api/consent":
            self._api_consent(body)
        elif self.path == "/portal/api/turn":
            self._api_turn(body)
        elif self.path == "/portal/api/feedback":
            self._api_feedback(body)
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def _api_session(self, body: dict) -> None:
        name = str(body.get("name") or "").strip()[:60]
        code = str(body.get("passcode") or "")
        if not name:
            self._send_json({"ok": False, "error": "Please enter your first name."}, 400)
            return
        if not hmac.compare_digest(code, self.invite_token):
            self._send_json({"ok": False, "error": "Invite code not recognized."}, 403)
            return
        with _lock:
            prune_sessions()
            session = create_session(name)
        append_turn(session, "system", f"session opened for subject '{name}'")
        self._send_json({"ok": True, "sessionId": session["id"]})

    def _api_consent(self, body: dict) -> None:
        session = self._session(str(body.get("sessionId") or ""))
        if session is None:
            self._send_json({"ok": False, "error": "Unknown or expired session."}, 404)
            return
        data, err = engine_post(
            self.engine_base,
            f"/api/v1/chat/{CHANDLER_ID}/consent",
            self.chandler_key,
            {"conversation_id": session["transcript"]["conversation_id"]},
        )
        if err is not None:
            self._send_json({"ok": False, "error": "Could not record consent — try again."}, 502)
            return
        session["consent_acknowledged"] = True
        append_turn(session, "system", "subject acknowledged reality-framing consent card")
        self._send_json({"ok": True})

    def _api_turn(self, body: dict) -> None:
        session = self._session(str(body.get("sessionId") or ""))
        message = str(body.get("message") or "").strip()[:MAX_MESSAGE_CHARS]
        if session is None:
            self._send_json({"ok": False, "error": "Unknown or expired session."}, 404)
            return
        if not message:
            self._send_json({"ok": False, "error": "Empty message."}, 400)
            return
        payload, status = handle_turn(session, message, self.chandler_key, self.engine_base)
        self._send_json(payload, status)

    def _api_feedback(self, body: dict) -> None:
        session = self._session(str(body.get("sessionId") or ""))
        notes = str(body.get("notes") or "").strip()[:4000]
        if session is None:
            self._send_json({"ok": False, "error": "Unknown or expired session."}, 404)
            return
        session["transcript"]["feedback"] = notes or None
        _write_transcript(session)
        append_turn(session, "system", "subject submitted session feedback")
        self._send_json({"ok": True})


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Chandler — private eval chat</title>
<style>
body{font-family:Georgia,serif;max-width:680px;margin:0 auto;background:#111;color:#eee;padding:18px 14px 60px}
h1{font-size:1.5em;border-bottom:1px solid #444;padding-bottom:8px}
.meta{color:#999;font-size:13px;line-height:1.5}
label{display:block;margin:14px 0 4px;font-size:14px;color:#bbb}
input,textarea,button{font:inherit}
input,textarea{width:100%;box-sizing:border-box;background:#1b1b1b;color:#eee;border:1px solid #444;border-radius:8px;padding:10px}
button{background:#33281f;border:1px solid #6b543c;color:#eee;border-radius:8px;padding:10px 18px;cursor:pointer;margin-top:12px}
button:disabled{opacity:.5;cursor:default}
#gate{margin-top:30px}
#chat{display:none;margin-top:20px}
.msg{padding:10px 14px;margin:6px 0;border-radius:12px;line-height:1.45;white-space:pre-wrap;overflow-wrap:break-word}
.user{background:#1d2a44}.chandler{background:#33281f}.system{color:#888;font-size:13px;background:none;padding:2px 6px}
#bar{display:flex;gap:8px;margin-top:14px}
#bar input{flex:1}
#card{display:none;background:#222;border:1px solid #6b543c;border-radius:12px;padding:16px;margin:16px 0}
#card h3{margin:0 0 8px}#card p{font-size:14px;line-height:1.5;color:#ccc;white-space:pre-wrap}
#err{color:#e08b8b;font-size:14px;min-height:1.2em;margin-top:8px}
#end{display:none;margin-top:30px;border-top:1px solid #444;padding-top:16px}
</style></head><body>
<h1>Chandler — private eval chat</h1>
<p class=meta>Invite-only evaluation surface. You are chatting with an AI persona.
Transcripts are saved for internal review. No channel outside this page is involved.</p>
<div id=gate>
<label>Your first name</label><input id=name maxlength=60 autocomplete=off>
<label>Invite code</label><input id=code type=password autocomplete=off>
<button id=start>Start chat</button><div id=err></div>
</div>
<div id=chat>
<div id=thread></div>
<div id=card><h3 id=cardTitle></h3><p id=cardBody></p>
<button id=ack>I understand — continue</button></div>
<div id=bar><input id=msg placeholder="Type a message…" maxlength=2000 autocomplete=off>
<button id=send>Send</button></div>
<div id=err></div>
<div id=end>
<label>Anything to add about how this felt? (optional)</label>
<textarea id=notes rows=3 maxlength=4000></textarea>
<button id=finish>End session</button><span id=done class=meta></span>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML};
let sessionId=null,pending=null,busy=false;
function err(m){$('err').textContent=m||''}
function add(role,text){const w=document.createElement('div');w.className='msg '+role;
w.innerHTML=(role==='user'?'<b>you:</b> ':role==='chandler'?'<b>chandler:</b> ':'')+esc(text);
$('thread').appendChild(w);window.scrollTo(0,document.body.scrollHeight)}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
let j={};try{j=await r.json()}catch(e){}
if(!r.ok&&!j.error)j.error='Request failed ('+r.status+')';return j}
$('start').onclick=async()=>{err('');
const j=await post('/portal/api/session',{name:$('name').value.trim(),passcode:$('code').value});
if(!j.ok){err(j.error);return}
sessionId=j.sessionId;$('gate').style.display='none';$('chat').style.display='block';
add('system','Session started. Say hi.');$('msg').focus()};
async function send(){if(busy||!sessionId)return;const text=$('msg').value.trim();if(!text)return;
$('msg').value='';err('');add('user',text);busy=true;$('send').disabled=true;
const j=await post('/portal/api/turn',{sessionId,message:text});
busy=false;$('send').disabled=false;
if(j.consentRequired){pending=text;$('cardTitle').textContent=j.card.title;
$('cardBody').textContent=j.card.body;$('card').style.display='block';return}
if(!j.ok){err(j.error);return}
add('chandler',j.text||'(no reply)')}
$('send').onclick=send;
$('msg').addEventListener('keydown',e=>{if(e.key==='Enter')send()});
$('ack').onclick=async()=>{const j=await post('/portal/api/consent',{sessionId});
$('card').style.display='none';if(!j.ok){err(j.error);return}
const text=pending;pending=null;if(text){$('msg').value=text;send()}};
$('finish').onclick=async()=>{const j=await post('/portal/api/feedback',{sessionId,notes:$('notes').value});
if(j.ok){$('done').textContent=' Thanks — session saved.';$('finish').disabled=true;$('msg').disabled=true;$('send').disabled=true}};
</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--invite-token", default=None)
    args = parser.parse_args()

    import os

    engine = args.engine or os.environ.get("HU2772_ENGINE", ENGINE)
    env_path = Path(args.env_file or os.environ.get("HU2772_ENV", str(ENV_FILE)))
    host = args.host or os.environ.get("PORTAL_HOST", "127.0.0.1")
    port = args.port or int(os.environ.get("PORTAL_PORT", "8765"))
    token = args.invite_token or os.environ.get("PORTAL_INVITE_TOKEN", "")
    if not token:
        token = secrets.token_urlsafe(12)

    key = resolve_chandler_key(env_path)

    PortalHandler.invite_token = token
    PortalHandler.chandler_key = key
    PortalHandler.engine_base = engine.rstrip("/")

    server = ThreadingHTTPServer((host, port), PortalHandler)
    print(f"chandler portal (HU-2772) listening on http://{host}:{port}", flush=True)
    print(f"engine: {engine}  env-file: {env_path}", flush=True)
    print(f"invite code: {token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
