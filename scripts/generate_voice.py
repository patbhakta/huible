#!/usr/bin/env python3
"""
Kestra worker — Persona voice generation (Option 4).

Generates persona voice lines with Gemini TTS routed through the home SOCKS
relay (Google is geo-blocked from the VPS; the relay at 100.83.231.16:1080 is
the proven path — same recipe as the OpenMAIC TTS provider).

Usage:
  python3 generate_voice.py --text "..." --out /path/out.mp3 \
      [--voice Charon] [--style-prompt "..."] [--model gemini-3.1-flash-tts-preview]

Reads GEMINI_API_KEY from the environment. Writes base64 outputs from the
Gemini API to an MP3 file. Exit 0 + JSON on success; exit 1 with message on
failure (so Kestra retries / surfaces the error cleanly).
"""

import argparse
import base64
import json
import os
import shutil
import socket
import sys
import urllib.request


def _key_from(names):
    """Try env first, then the Kestra worker env file."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    try:
        with open("/opt/kestra/kestra.env", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    if k in names:
                        return v
    except OSError:
        pass
    return None


RELAY_HOST = "100.83.231.16"
RELAY_PORT = 1080


class RelaySocket:
    """Opens a connection to the home relay and performs the SOCKS5 CONNECT
    handshake to a target host. Returns a plain connected socket (subclass of
    socket.socket) usable directly by ssl.wrap_socket / http.client."""

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def connect_to(self, target_host, target_port):
        s = socket.create_connection((self.host, self.port), timeout=20)
        try:
            # SOCKS5 greeting: one method, no auth
            s.sendall(b"\x05\x01\x00")
            resp = s.recv(2)
            if resp != b"\x05\x00":
                raise OSError("relay refused SOCKS5 no-auth")
            # SOCKS5 CONNECT by domain name
            addr = target_host.encode("utf-8")
            req = b"\x05\x01\x00\x03" + bytes([len(addr)]) + addr + target_port.to_bytes(2, "big")
            s.sendall(req)
            resp = s.recv(10)
            if len(resp) < 2 or resp[1] != 0:
                raise OSError(f"SOCKS CONNECT failed: {resp!r}")
            return s
        except Exception:
            s.close()
            raise


def http_post_via_relay(url, headers, body):
    """POST via the SOCKS relay: SOCKS5 CONNECT tunnel + TLS wrap +
    http.client request."""
    import http.client
    import ssl
    from urllib.parse import urlparse

    parsed = urlparse(url)
    raw = RelaySocket(RELAY_HOST, RELAY_PORT).connect_to(parsed.hostname, 443)
    raw.settimeout(180)
    ctx = ssl.create_default_context()
    tls_sock = ctx.wrap_socket(raw, server_hostname=parsed.hostname)
    conn = http.client.HTTPSConnection(parsed.hostname, 443, timeout=180)
    conn.sock = tls_sock  # inject the TLS-wrapped relayed socket
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    conn.close()
    return status, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default="Charon")
    ap.add_argument("--style-prompt", default="")
    ap.add_argument("--model", default="gemini-3.1-flash-tts-preview")
    args = ap.parse_args()

    api_key = _key_from(("GEMINI_API_KEY",))
    if not api_key:
        print(json.dumps({"ok": False, "error": "GEMINI_API_KEY not set"}))
        sys.exit(1)

    style = args.style_prompt or (
        "Style: Warm mid-30s American male, witty and quick, sardonic but kind. "
        "Pacing: brisk, comedic timing, slight pause before punchlines."
    )
    payload = {
        "contents": [{"parts": [{"text": f"{style}\n\nSay exactly: {args.text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": args.voice}}},
        },
        "model": args.model,
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{args.model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    try:
        status, data = http_post_via_relay(url, headers, json.dumps(payload).encode("utf-8"))
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"relay/http failure: {e}"}))
        sys.exit(1)

    if status != 200:
        print(json.dumps({"ok": False, "error": f"HTTP {status}: {data.decode('utf-8', 'replace')[:400]}"}))
        sys.exit(1)

    body = json.loads(data.decode("utf-8"))
    try:
        b64 = body["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        mime = body["candidates"][0]["content"]["parts"][0]["inlineData"].get("mimeType", "audio/mp3")
    except (KeyError, IndexError):
        print(json.dumps({"ok": False, "error": f"unexpected response: {json.dumps(body)[:400]}"}))
        sys.exit(1)

    audio = base64.b64decode(b64)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    raw_out = args.out
    # Gemini TTS returns raw L16/24kHz PCM regardless of file extension —
    # convert to a playable MP3 with ffmpeg when available.
    converted = False
    if shutil.which("ffmpeg"):
        raw_out = args.out + ".pcm"
        converted = True
    with open(raw_out, "wb") as f:
        f.write(audio)
    if converted:
        import subprocess as sp
        proc = sp.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000",
             "-ac", "1", "-i", raw_out, "-codec:a", "libmp3lame", "-b:a", "128k", args.out],
            capture_output=True, text=True, timeout=120,
        )
        os.unlink(raw_out)
        if proc.returncode != 0:
            print(json.dumps({"ok": False, "error": f"ffmpeg failed: {proc.stderr[:300]}"}))
            sys.exit(1)

    size = os.path.getsize(args.out)
    if size < 1000:
        print(json.dumps({"ok": False, "error": f"audio too small ({size}B) — suspect failure"}))
        sys.exit(1)
    print(json.dumps({
        "ok": True,
        "out": args.out,
        "bytes": size,
        "mime": mime,
        "voice": args.voice,
        "model": args.model,
        "route": f"socks5://{RELAY_HOST}:{RELAY_PORT}",
        "format": "mp3 (converted from PCM)" if converted else mime,
    }))


if __name__ == "__main__":
    main()
