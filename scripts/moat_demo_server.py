#!/usr/bin/env python3
# ruff: noqa: E501, RUF001
# (self-contained HTML/CSS/JS page below — line length and typographic
# punctuation are inherent to the embedded page, everything else is linted)
"""HUible moat demo — the memory layer, visible in a live conversation (HU-2793).

One tailnet-only page the founder opens in a browser. Four panes, all real:

1. LIVE CHAT — every turn goes through the REAL engine (G1/G6/G8 stack, ZAI
   persona voice, vault retrieval) exactly like production.
2. MEMORY X-RAY — inline under every reply, verbatim:
   - what was WRITTEN to TencentDB this turn (the L0 rows + synced flag),
   - the WORKING-MEMORY block the Arm A recall actually injected into the
     prompt (session-gist digest + verbatim excerpts, from the trace),
   - which VAULT notes were retrieved to ground the reply (type, activation
     score, snippet) and which scoped vault lanes fired.
3. AMNESIA TEST — "wipe page & ask what I said first": clears the browser
   state, resumes the SAME store-backed session and sends the ordinal probe
   ("What was the very first thing I said to you?"). The deterministic
   zero-LLM ordinal lane answers verbatim from TencentDB L0 rows — turn 1 is
   long outside the 10-turn in-process window, so a correct answer cannot be
   the context window. A second button ("new conversation") mints a fresh
   isolated session: memory must NOT leak there — the 2026-08-16 isolation
   doctrine shown as the safety guard it is.
4. KILL SWITCH — toggle the W4 TencentDB lane off per-turn
   (working_memory_enabled=false): recall empty, capture skipped, replies
   degrade. The delta IS the proof the lane is load-bearing.

If retrieval fails, the page shows it failing (empty block, unsettled
digest, integrity-gate rejections). Diagnostic, not sales pitch.

Never touches the store except through the engine's own API and read-only
gateway /recall probes with the engine's exact session keys.

Run:  HUIBLE_DEMO_KEY=<key> python3 scripts/moat_demo_server.py   (port 8097)
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = os.environ.get("HUIBLE_API_BASE", "http://127.0.0.1:8000/api/v1")
PORT = int(os.environ.get("HUIBLE_MOAT_PORT", "8097"))
# Tailnet-only bind: reachable from Pat's devices on the mesh, unreachable
# from the public internet (same posture as the onboarding demo on :8098).
BIND = os.environ.get("HUIBLE_MOAT_BIND", "100.101.235.117")
ACCESS_TOKEN = os.environ.get("HUIBLE_DEMO_TOKEN", "huible-preview")

CHANDLER_KEY = os.environ.get("HUIBLE_DEMO_KEY", "")
PERSONA_ID = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"
PERSONA_NAME = "Chandler"

# Working-memory gateway (read-only /recall probes for the store inspector).
WM_GATEWAY = os.environ.get("HUIBLE_WM_GATEWAY", "http://127.0.0.1:8420")
WM_SERVICE_ID = os.environ.get("HUIBLE_WM_SERVICE_ID", "huible-chandler")


def session_key_for(conversation_id: str) -> str:
    """The engine's exact working-memory session key for this conversation."""
    return f"huible-p{PERSONA_ID}-c{conversation_id}"


def api_call(method, path, body=None, key=CHANDLER_KEY, timeout=120, internal=True):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if internal and path.startswith("/chat") and not path.endswith("/consent"):
        # Sanctioned internal/probe lane (real_user_gate.py): skips only the
        # ramp-gate refusal; G1 crisis, G6 consent, G8 risk still fully fire.
        headers["X-Huible-Traffic-Class"] = "internal"
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"raw": str(e)}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def gateway_recall(session_key, query):
    """Read-only Arm A probe — the same read the engine performs per turn."""
    body = json.dumps({"query": query[:2000], "session_key": session_key}).encode()
    req = urllib.request.Request(
        WM_GATEWAY + "/recall",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-tdai-service-id": WM_SERVICE_ID},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"code": -1, "message": f"{type(e).__name__}: {e}"}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HUible — The Moat, Live (memory layer X-ray)</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0d1117; color: #e6edf3; display: flex; min-height: 100vh; flex-direction: column; }
  header { padding: 12px 20px; background: #161b22; border-bottom: 1px solid #30363d;
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  header .logo { font-weight: 700; font-size: 18px; color: #58a6ff; }
  header .tag { font-size: 12px; color: #8b949e; }
  header .pill { font-size: 12px; padding: 4px 10px; border-radius: 999px;
    background: #1f6feb33; color: #79c0ff; border: 1px solid #1f6feb55; }
  .spacer { margin-left: auto; }
  .banner { padding: 10px 16px; background: #2d1b00; color: #e3b341; font-size: 12.5px;
    border-bottom: 1px solid #9e6a03; display: none; }
  main { flex: 1; display: flex; flex-wrap: wrap; }
  .chat-col { flex: 3 1 520px; display: flex; flex-direction: column; min-width: 360px;
    border-right: 1px solid #30363d; }
  .side-col { flex: 1 1 300px; min-width: 280px; }
  #log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
  .msg { max-width: 82%; padding: 9px 14px; border-radius: 14px; line-height: 1.45; white-space: pre-wrap; }
  .msg.user { align-self: flex-end; background: #1f6feb; color: white; border-bottom-right-radius: 4px; }
  .msg.bot  { align-self: flex-start; background: #21262d; border: 1px solid #30363d; border-bottom-left-radius: 4px; }
  .msg.sys  { align-self: center; background: #161b22; border: 1px dashed #30363d; color: #8b949e;
    font-size: 12.5px; max-width: 95%; text-align: center; }
  .msg.err  { align-self: center; color: #f85149; font-size: 12.5px; }
  .msg.fake { border-color: #9e6a03; }
  /* X-ray card — inline under each reply */
  .xray { align-self: flex-start; width: 96%; margin: 2px 0 6px; background: #0b1420;
    border: 1px solid #1f4470; border-radius: 10px; padding: 10px 12px; font-size: 12.5px; }
  .xray h5 { margin: 0 0 6px; font-size: 10.5px; letter-spacing: .1em; text-transform: uppercase;
    color: #58a6ff; }
  .xray h5.off { color: #f85149; }
  .xray .lane { margin: 6px 0 0; }
  .xray .lane b { color: #8b949e; font-weight: 600; }
  .xray pre { white-space: pre-wrap; word-break: break-word; background: #010409;
    border: 1px solid #21262d; border-radius: 6px; padding: 8px; margin: 4px 0 0;
    font-size: 11.5px; color: #c9d1d9; max-height: 180px; overflow-y: auto; }
  .xray .snip { color: #8b949e; font-size: 12px; }
  .xray .score { color: #3fb950; font-family: ui-monospace, monospace; font-size: 11px; }
  .xray .kv { padding: 2px 0; }
  .xray details { margin-top: 6px; } .xray summary { cursor: pointer; color: #58a6ff; font-size: 11.5px; }
  .ok { color: #3fb950; } .warn { color: #d29922; } .bad { color: #f85149; } .dim { color: #8b949e; }
  .composer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid #30363d; background: #161b22; }
  .composer input { flex: 1; padding: 10px 14px; border-radius: 8px; border: 1px solid #30363d;
    background: #0d1117; color: #e6edf3; font-size: 14px; outline: none; }
  .composer button { padding: 10px 18px; border-radius: 8px; border: 0; background: #238636;
    color: white; font-weight: 600; cursor: pointer; }
  .composer button:disabled { background: #21262d; color: #8b949e; }
  .side-col h3 { margin: 0; padding: 14px 16px 6px; font-size: 12px; letter-spacing: .08em;
    color: #8b949e; text-transform: uppercase; }
  .card { margin: 0 12px 12px; padding: 12px; background: #161b22; border: 1px solid #30363d;
    border-radius: 10px; font-size: 13px; }
  .card pre { white-space: pre-wrap; word-break: break-word; background: #010409; border: 1px solid #21262d;
    border-radius: 6px; padding: 8px; font-size: 11px; color: #c9d1d9; max-height: 220px; overflow-y: auto; }
  .card .kv { display: flex; justify-content: space-between; gap: 8px; padding: 2px 0; font-size: 12.5px; }
  .card .kv span:first-child { color: #8b949e; }
  .btnrow { display: flex; flex-direction: column; gap: 8px; }
  .btnrow button { padding: 10px 14px; border-radius: 8px; border: 1px solid #30363d;
    background: #21262d; color: #e6edf3; font-weight: 600; cursor: pointer; font-size: 13px; text-align: left; }
  .btnrow button.amnesia { background: #da3633; }
  .btnrow button:disabled { opacity: .5; cursor: wait; }
  .toggle { display: flex; align-items: center; gap: 10px; }
  .switch { position: relative; width: 44px; height: 24px; border-radius: 999px; background: #238636;
    border: 0; cursor: pointer; }
  .switch.off { background: #6e7681; }
  .switch::after { content: ''; position: absolute; top: 3px; left: 24px; width: 18px; height: 18px;
    border-radius: 50%; background: white; transition: left .15s; }
  .switch.off::after { left: 3px; }
  .hint { font-size: 11.5px; color: #8b949e; margin-top: 8px; line-height: 1.5; }
  .consentbox { max-width: 92%; align-self: center; margin: 8px 0; }
  .consentbox .inner { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 18px; }
  .consentbox h4 { margin: 0 0 10px; font-size: 15px; }
  .consentbox p { font-size: 13px; color: #c9d1d9; line-height: 1.55; margin: 0 0 10px; }
  .consentbox button { margin-top: 6px; padding: 10px 18px; border-radius: 8px; border: 0;
    background: #238636; color: white; font-weight: 600; cursor: pointer; font-size: 14px; }
</style>
</head>
<body>
<header>
  <span class="logo">HUible</span>
  <span class="tag">The moat, live — every reply annotated with the memory actually used</span>
  <span class="pill" id="state">connecting…</span>
</header>
<div class="banner" id="banner"></div>
<main>
  <div class="chat-col">
    <div id="log"></div>
    <div class="composer">
      <input id="inp" placeholder="Say something to Chandler…" autocomplete="off" disabled>
      <button id="send" disabled>Send</button>
    </div>
  </div>
  <div class="side-col">
    <h3>Moat controls</h3>
    <div class="card">
      <div class="btnrow">
        <div class="toggle">
          <button class="switch" id="wmswitch" title="TencentDB working-memory lane"></button>
          <div><b id="wmlabel">Memory ON</b>
            <div class="hint" id="wmhint">TencentDB lane armed. Flip off and ask what only the store knows — watch recall degrade.</div>
          </div>
        </div>
        <button id="amnesia">🧠 Amnesia test — wipe page, ask &ldquo;what did I say first?&rdquo;</button>
        <button id="isolation">🛡 New conversation (strict isolation guard)</button>
        <button id="resume">↩ Resume latest store session</button>
      </div>
      <div class="hint"><b>2-minute read:</b> type a fact only you know (e.g. a secret word).
        The X-ray under the reply shows it being <b>written to TencentDB</b> (L0) and the
        <b>working-memory block</b> injected into the prompt, verbatim. Click
        <b>Amnesia test</b>: the page is wiped and the persona recalls your first line from the
        store — turn 1 is far outside the model's context window, so a correct answer can only
        come from TencentDB. Flip the <b>kill switch</b> and the same question goes generic.</div>
    </div>
    <h3>Store inspector (live gateway read)</h3>
    <div class="card" id="store-card"><span class="warn">awaiting first turn…</span></div>
    <h3>Session</h3>
    <div class="card" id="session-card"></div>
    <h3>What this proves</h3>
    <div class="card" id="about-card"></div>
  </div>
</main>
<script>
const API = "";
const PERSONA_ID = "__PERSONA_ID__";
const PERSONA_NAME = "__PERSONA_NAME__";
let CONV = null, BUSY = false, MEMORY_ON = true, TURN = 0;

const log = document.getElementById('log');
const inp = document.getElementById('inp');
const send = document.getElementById('send');
const statePill = document.getElementById('state');
const banner = document.getElementById('banner');
const wmSwitch = document.getElementById('wmswitch');

function addMsg(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}
function addNode(node) { log.appendChild(node); log.scrollTop = log.scrollHeight; }
function setBanner(text) { banner.textContent = text; banner.style.display = text ? 'block' : 'none'; }
function esc(s) { const d = document.createElement('span'); d.textContent = s || ''; return d.innerHTML; }
function kv(k, v) { return '<div class="kv"><span>' + k + '</span><span>' + v + '</span></div>'; }

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: {'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined
  });
  let j = {};
  try { j = await r.json(); } catch (e) {}
  return { status: r.status, data: j };
}

/* ---------- X-ray ---------- */
function xrayCard(userText, reply, trace, opts) {
  const off = !!(opts && opts.laneOff);
  const d = document.createElement('div');
  d.className = 'xray';
  let h = '<h5' + (off ? ' class="off"' : '') + '>X-ray · turn ' + (++TURN) +
    (off ? ' — W4 LANE OFF (kill switch)' : '') + '</h5>';

  // Lane 1: what was written to TencentDB this turn
  const wm = trace.working_memory;
  h += '<div class="lane"><b>1 · WRITE → TencentDB L0</b> ';
  if (off) {
    h += '<span class="bad">skipped (lane disabled this turn)</span>';
  } else if (!wm) {
    h += '<span class="warn">lane unavailable this turn</span>';
  } else if (wm.synced) {
    h += '<span class="ok">committed ✓</span> <span class="snip">user: “' +
      esc(userText.slice(0, 70)) + (userText.length > 70 ? '…' : '') + '” · assistant: “' +
      esc(reply.slice(0, 70)) + (reply.length > 70 ? '…' : '') + '”</span>';
  } else {
    h += '<span class="bad">NOT written</span> <span class="snip">(capture failed or integrity gate rejected)</span>';
  }
  h += '</div>';

  // Lane 2: the working-memory block actually injected into the prompt
  h += '<div class="lane"><b>2 · READ → injected working-memory block</b> ';
  if (off) {
    h += '<span class="bad">none — recall disabled</span>';
  } else if (!wm) {
    h += '<span class="warn">no W4 view on trace</span>';
  } else if (wm.context && wm.context.length) {
    h += '<span class="ok">' + wm.chars + ' chars · ' + esc(wm.strategy) + '</span>';
    h += '<pre>' + esc(wm.context) + '</pre>';
  } else {
    h += '<span class="warn">EMPTY this turn — strategy ' + esc(wm.strategy || '?') +
      ', gist blocks settled: ' + (wm.gist_blocks ?? '?') +
      '</span><div class="snip">Nothing recalled from the store for this turn (extraction ' +
      'pipeline lag or nothing session-scoped to serve). This is what the lane failing looks like.</div>';
  }
  h += '</div>';

  // Lane 3: vault retrieval grounding
  const mems = trace.activated_memories || [];
  h += '<div class="lane"><b>3 · VAULT RETRIEVAL</b> <span class="ok">' + mems.length +
    ' memories activated</span>';
  if (mems.length) {
    h += '<details><summary>show the vault notes grounding this reply</summary>';
    mems.slice(0, 8).forEach(m => {
      const c = (m.content || '').replace(/\s+/g, ' ');
      h += '<div class="kv"><span class="dim">[' + esc(m.content_type) + ' · ' +
        esc(m.disclosure_scope || '') + ']</span> <span class="score">' +
        Number(m.activation_score || 0).toFixed(3) + '</span></div>' +
        '<div class="snip">“' + esc(c.slice(0, 160)) + (c.length > 160 ? '…' : '') + '”</div>';
    });
    if (mems.length > 8) h += '<div class="snip dim">…+' + (mems.length - 8) + ' more (raw trace)</div>';
    h += '</details>';
  }
  const sr = trace.scoped_reads || [];
  if (sr.length) {
    h += '<div class="snip">scoped vault lanes: ' +
      sr.map(s => esc(s.section) + '×' + s.lines).join(', ') + '</div>';
  }
  if (trace.interest_tool) h += '<div class="snip">interest lane: ' + trace.interest_tool.lines + ' lines</div>';
  const exc = trace.exclusion_counts || {};
  const excKeys = Object.keys(exc);
  if (excKeys.length) {
    h += '<div class="snip dim">firewall excluded: ' +
      excKeys.map(k => k + '×' + exc[k]).join(', ') + '</div>';
  }
  h += '</div>';

  // Provenance strip
  h += '<details><summary>raw trace</summary><pre>' + esc(JSON.stringify(trace, null, 1)) + '</pre></details>';
  d.innerHTML = h;
  return d;
}

/* ---------- store inspector ---------- */
async function refreshStore(query) {
  if (!CONV) return;
  const r = await api('GET', '/demo/store?conversation_id=' + encodeURIComponent(CONV) +
    '&q=' + encodeURIComponent(query || 'session digest'));
  const el = document.getElementById('store-card');
  if (r.status !== 200) {
    el.innerHTML = '<span class="bad">gateway probe failed: ' + esc(JSON.stringify(r.data).slice(0, 140)) + '</span>';
    return;
  }
  const d = r.data;
  let h = kv('strategy', '<span class="ok">' + esc(d.strategy || '—') + '</span>') +
    kv('digest settled', d.digest_settled == null ? '—' : (d.digest_settled ? '<span class="ok">yes</span>' : 'no')) +
    kv('gist blocks', d.gist_blocks == null ? '—' : d.gist_blocks) +
    kv('serves now', (d.chars || 0) + ' chars');
  h += '<details open><summary>current recall payload</summary><pre>' +
    esc(d.context || '(empty — nothing served for this query)') + '</pre></details>';
  el.innerHTML = h;
}

/* ---------- session card ---------- */
function renderSession() {
  document.getElementById('session-card').innerHTML =
    kv('Conversation', CONV ? '<span class="ok">' + esc(CONV.slice(0, 14)) + '…</span>' : '—') +
    kv('Persona', esc(PERSONA_NAME)) +
    kv('Store key', CONV ? '<span class="dim">huible-p…-c' + esc(CONV.slice(0, 10)) + '…</span>' : '—') +
    kv('W4 lane', MEMORY_ON ? '<span class="ok">ON</span>' : '<span class="bad">OFF (kill switch)</span>') +
    kv('Persisted', localStorage.getItem('moat_conv') === CONV && CONV ? 'yes (survives reloads)' : 'no');
}

/* ---------- boot / consent ---------- */
async function boot() {
  const saved = localStorage.getItem('moat_conv');
  addMsg('sys', saved ?
    ('Resuming store session ' + saved.slice(0, 10) + '… — use “Resume” or start typing to continue it.') :
    'Starting a fresh session…');
  const r = await api('POST', '/demo/session');
  if (r.status !== 200) {
    addMsg('err', 'Could not reach the HUible API: ' + JSON.stringify(r.data).slice(0, 200));
    statePill.textContent = 'API unreachable'; statePill.style.color = '#f85149';
    return;
  }
  if (!saved) { CONV = r.data.conversation_id; localStorage.setItem('moat_conv', CONV); }
  else { CONV = saved; }
  renderSession();
  // First turn without consent -> the real 409 card (the real G6 gate firing)
  const first = await api('POST', '/demo/turn', { message: 'Hey Chandler, you there?', conversation_id: CONV });
  if (first.status === 409 && first.data.consent_card) {
    renderConsent(first.data.consent_card);
  } else if (first.status === 200) {
    onReply('Hey Chandler, you there?', first.data);
  } else {
    addMsg('err', 'First-turn response ' + first.status + ': ' + JSON.stringify(first.data).slice(0, 160));
  }
}

function renderConsent(card) {
  const w = document.createElement('div');
  w.className = 'consentbox';
  w.innerHTML = '<div class="inner"><h4>' + esc(card.title || 'Before we begin') + '</h4>' +
    '<p>' + esc(card.body || '').replace(/\n/g, '<br><br>') + '</p>' +
    '<p style="color:#8b949e;font-size:12px">' + esc(card.acknowledge_instructions || '') + '</p>' +
    '<button id="ack">I understand — begin</button></div>';
  addNode(w);
  document.getElementById('ack').onclick = async () => {
    const r = await api('POST', '/demo/consent', { conversation_id: CONV });
    if (r.status === 200) {
      w.querySelector('.inner').style.opacity = .55;
      addMsg('sys', 'Consent recorded (G6, real gate) — chat unlocked.');
      unlock();
    } else {
      addMsg('err', 'Consent failed: ' + JSON.stringify(r.data).slice(0, 160));
    }
  };
}

function unlock() { inp.disabled = false; send.disabled = false; inp.focus(); }

/* ---------- turns ---------- */
function onReply(userText, data, opts) {
  const reply = data.response || '';
  const bot = addMsg('bot', reply);
  if ((data.trace && data.trace.provider || '').includes('fake')) bot.classList.add('fake');
  addNode(xrayCard(userText, reply, data.trace || {}, opts));
  renderSession();
  refreshStore(opts && opts.inspectQuery ? opts.inspectQuery : userText);
}

async function turn(text, opts) {
  if (BUSY) return;
  opts = opts || {};
  BUSY = true; send.disabled = true; inp.disabled = true;
  statePill.textContent = 'thinking…';
  addMsg('user', text);
  const body = { message: text, conversation_id: CONV };
  if (!MEMORY_ON) body.working_memory_enabled = false;
  const r = await api('POST', '/demo/turn', body);
  BUSY = false; send.disabled = false; inp.disabled = false; inp.focus();
  statePill.textContent = 'chat live';
  if (r.status === 200) {
    onReply(text, r.data, { laneOff: !MEMORY_ON, inspectQuery: opts.inspectQuery });
  } else if (r.status === 409) {
    renderConsent((r.data && r.data.consent_card) || {});
  } else if (r.status === 429) {
    addMsg('sys', 'Dosage cap reached (G8) — safety feature, session paused.');
  } else {
    addMsg('err', r.status + ': ' + JSON.stringify(r.data).slice(0, 200));
  }
}

function doTurn() {
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  turn(text);
}

/* ---------- moat buttons ---------- */
const AMNESIA_Q = 'What was the very first thing I said to you?';

document.getElementById('amnesia').onclick = async () => {
  if (!CONV || BUSY) return;
  log.innerHTML = '';
  TURN = 0;
  addMsg('sys', 'Page wiped. Conversation id unchanged — the store still holds every turn. ' +
    'Now asking what was said first…');
  turn(AMNESIA_Q, { inspectQuery: AMNESIA_Q });
};

document.getElementById('isolation').onclick = async () => {
  if (BUSY) return;
  const r = await api('POST', '/demo/session');
  if (r.status !== 200) { addMsg('err', 'Could not mint a session'); return; }
  CONV = r.data.conversation_id;
  localStorage.removeItem('moat_conv');
  log.innerHTML = '';
  TURN = 0;
  addMsg('sys', 'NEW conversation id → new isolated working-memory scope (the 2026-08-16 ' +
    'contamination guard). The persona must NOT know earlier sessions here.');
  turn(AMNESIA_Q, { inspectQuery: AMNESIA_Q });
};

document.getElementById('resume').onclick = async () => {
  const saved = localStorage.getItem('moat_conv');
  if (BUSY) return;
  if (!saved) { addMsg('sys', 'No saved session on this device — tell Chandler something first.'); return; }
  CONV = saved;
  log.innerHTML = '';
  TURN = 0;
  addMsg('sys', 'Resumed store session ' + saved.slice(0, 10) + '… — the window was empty, the store was not.');
  turn(AMNESIA_Q, { inspectQuery: AMNESIA_Q });
};

wmSwitch.onclick = () => {
  MEMORY_ON = !MEMORY_ON;
  wmSwitch.classList.toggle('off', !MEMORY_ON);
  document.getElementById('wmlabel').textContent = MEMORY_ON ? 'Memory ON' : 'Memory OFF — kill switch';
  document.getElementById('wmhint').textContent = MEMORY_ON ?
    'TencentDB lane armed. Flip off and ask what only the store knows — watch recall degrade.' :
    'KILL SWITCH: this turn sent working_memory_enabled=false. Recall empty, capture skipped, vault lanes still on.';
  renderSession();
};

document.getElementById('about-card').innerHTML =
  kv('Pane 1 — chat', 'real engine, real path (G1/G6/G8, ZAI voice)') +
  kv('Pane 2 — X-ray', 'verbatim injected blocks + writes + vault notes, per turn') +
  kv('Pane 3 — amnesia', 'recall from TencentDB after the page is wiped') +
  kv('Pane 4 — kill switch', 'lane off → recall degrades; the delta is the moat') +
  '<div class="hint">If retrieval fails anywhere, the X-ray shows the failure ' +
  '(empty block, NOT written, unsettled digest). Diagnostic, not sales pitch.</div>';

send.onclick = doTurn;
inp.addEventListener('keydown', e => { if (e.key === 'Enter') doTurn(); });
setInterval(refreshStore, 30000, 'session digest');
boot();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else str(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html") and ACCESS_TOKEN not in self.path:
            return self._send(401, "token required: append ?t=" + ACCESS_TOKEN, "text/plain")
        if path in ("/", "/index.html"):
            html = PAGE.replace("__PERSONA_ID__", PERSONA_ID).replace(
                "__PERSONA_NAME__", PERSONA_NAME
            )
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/demo/health":
            st, body = api_call("GET", "/health")
            self._send(200 if st == 200 else 502, {"upstream": st, "api": body.get("data", body)})
        elif path == "/demo/store":
            if not CHANDLER_KEY:
                return self._send(500, {"error": "server missing HUIBLE_DEMO_KEY"})
            conv = (
                self.path.split("conversation_id=")[-1].split("&")[0]
                if "conversation_id=" in self.path
                else ""
            )
            q = "session digest"
            if "q=" in self.path:
                from urllib.parse import unquote_plus

                q = unquote_plus(self.path.split("q=")[-1].split("&")[0])
            if not conv:
                return self._send(400, {"error": "conversation_id required"})
            d = gateway_recall(session_key_for(conv), q)
            ctx = d.get("prepend_context") or ""
            self._send(
                200,
                {
                    "session_key": session_key_for(conv),
                    "strategy": d.get("strategy"),
                    "digest_settled": d.get("digest_settled"),
                    "gist_blocks": d.get("gist_blocks"),
                    "chars": len(ctx),
                    "context": ctx,
                    "gateway_code": d.get("code"),
                    "gateway_message": d.get("message"),
                },
            )
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = {}
        if length:
            try:
                payload = json.loads(self.rfile.read(length).decode())
            except Exception:
                return self._send(400, {"error": "bad json"})
        if self.path == "/demo/session":
            conv = "moat-" + uuid.uuid4().hex[:12]
            st, health = api_call("GET", "/health")
            gen = (health.get("data", {}) or {}).get("checks", {}).get("generator", "unknown")
            self._send(200, {"conversation_id": conv, "generator": gen})
        elif self.path == "/demo/turn":
            msg = (payload.get("message") or "").strip()
            if not msg:
                return self._send(400, {"error": "message required"})
            conv = payload.get("conversation_id") or ""
            chat_body = {
                "message": msg,
                "relationship": "close_friend",
                "conversation_id": conv,
            }
            # HU-2793 kill switch: only send the field when the client
            # explicitly turned the lane off; omit otherwise (engine default).
            if payload.get("working_memory_enabled") is False:
                chat_body["working_memory_enabled"] = False
            st, body = api_call("POST", f"/chat/{PERSONA_ID}", chat_body)
            if st in (200, 409, 429):
                if st == 409:
                    card, detail = {}, ""
                    d = body.get("detail") or {}
                    if isinstance(d, dict):
                        e = d.get("error") or d
                        card = e.get("consent_card") or e.get("card") or {}
                        detail = e.get("message") or ""
                    return self._send(409, {"consent_card": card, "detail": detail})
                self._send(st, body)
            else:
                self._send(st if st else 502, body)
        elif self.path == "/demo/consent":
            conv = payload.get("conversation_id") or ""
            st, body = api_call("POST", f"/chat/{PERSONA_ID}/consent", {"conversation_id": conv})
            self._send(st, body)
        else:
            self._send(404, {"error": "not found"})


def main():
    if not CHANDLER_KEY:
        print("FATAL: HUIBLE_DEMO_KEY not set — source the demo env first")
        raise SystemExit(1)
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(
        f"moat demo on http://{BIND}:{PORT} (persona {PERSONA_ID}, gateway {WM_GATEWAY} svc {WM_SERVICE_ID})"
    )
    srv.serve_forever()


if __name__ == "__main__":
    main()
