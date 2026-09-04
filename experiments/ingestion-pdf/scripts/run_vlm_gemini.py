"""VLM-assisted extraction via Gemini vision (gemini-3.8-flash) on rasterized pages.

Boss approval 2026-09-04: gemini-3.8-flash for the VLM extraction leg
(flash-only ladder, no pro-tier). Two transports:

- relay (default, production lane): Google generativelanguage API via the home
  SOCKS5 relay (pat-w11pc 100.83.231.16:1080 — the proven path, same recipe as
  scripts/generate_voice.py). Google API is geo-blocked from the VPS. Measured
  2026-09-04 ~19:40Z: relay accepts SOCKS greeting but closes every CONNECT
  (0/16 attempts, all targets) — home-side egress outage; lane is resumable.
- openrouter (measurement lane): existing metered OpenRouter channel
  (OPENROUTER_MONTHLY_BUDGET_USD, spend-state tracked) — direct from the VPS,
  model google/gemini-3.8-flash. Used to measure extraction quality while the
  relay is down; no new spend class (same approved model, ~cents for pages).

Ops design per boss note: batchable + resumable — each page result is written
to disk immediately; already-written outputs are skipped on re-run.

Keys read from env, then /opt/kestra/kestra.env (relay) or repo .env
(openrouter). Never hardcoded.
"""
import base64
import http.client
import json
import os
import pathlib
import socket
import ssl
import time
import urllib.request
from urllib.parse import urlparse

BASE = pathlib.Path(__file__).resolve().parent.parent

PROMPT = (
    "Extract all content from this document page verbatim: body text, headings, "
    "tables as markdown, formulas as LaTeX, and describe charts briefly in brackets. "
    "Output only the extracted content, no commentary."
)

RELAY_HOST = "100.83.231.16"
RELAY_PORT = 1080
GOOGLE_MODEL = "gemini-3.8-flash"
OPENROUTER_MODEL = "google/gemini-3.8-flash"


def transport() -> str:
    t = os.environ.get("VAULT_VLM_TRANSPORT", "relay").lower()
    if t not in ("relay", "openrouter"):
        raise SystemExit(f"unknown VAULT_VLM_TRANSPORT: {t}")
    return t


def out_dir() -> pathlib.Path:
    d = BASE / "outputs" / ("vlm_gemini" if transport() == "relay" else "vlm_gemini_or")
    d.mkdir(parents=True, exist_ok=True)
    return d


def key_from(*names_and_paths):
    for names, path in names_and_paths:
        for n in names:
            if os.environ.get(n):
                return os.environ[n]
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        if k in names and v:
                            return v
        except OSError:
            pass
    return None


def connect_via_relay(host, port, attempts=3):
    """SOCKS5 CONNECT through the home relay; returns the tunneled raw socket.

    The relay (pat-w11pc, Windows desktop) occasionally closes a tunnel
    attempt immediately — retry with backoff.
    """
    last: object = None
    for i in range(attempts):
        try:
            s = socket.create_connection((RELAY_HOST, RELAY_PORT), timeout=20)
            s.settimeout(300)
            s.sendall(b"\x05\x01\x00")
            if s.recv(2) != b"\x05\x00":
                raise OSError("relay refused SOCKS5 no-auth")
            addr = host.encode()
            s.sendall(b"\x05\x01\x00\x03" + bytes([len(addr)]) + port.to_bytes(2, "big"))
            resp = b""
            while len(resp) < 4:
                chunk = s.recv(10 - len(resp))
                if not chunk:
                    raise OSError("SOCKS CONNECT closed by relay (egress down?)")
                resp += chunk
            if resp[1] != 0:
                s.close()
                raise OSError(f"SOCKS CONNECT failed: {resp!r}")
            return s
        except (TimeoutError, OSError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def http_post_via_relay(url, headers, body):
    parsed = urlparse(url)
    raw = connect_via_relay(parsed.hostname, 443)
    ctx = ssl.create_default_context()
    tls_sock = ctx.wrap_socket(raw, server_hostname=parsed.hostname)
    conn = http.client.HTTPSConnection(parsed.hostname, 443, timeout=300)
    conn.sock = tls_sock
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    conn.close()
    return status, data


def build_parts(png_b64: str):
    return [
        {"inline_data": {"mime_type": "image/png", "data": png_b64}},
        {"text": PROMPT},
    ]


def call_relay(png_b64: str, api_key: str):
    body = json.dumps(
        {
            "contents": [{"parts": build_parts(png_b64)}],
            "generationConfig": {"maxOutputTokens": 6000, "temperature": 0},
        }
    ).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GOOGLE_MODEL}:generateContent?key={api_key}"
    t0 = time.perf_counter()
    status, data = http_post_via_relay(
        url, {"Content-Type": "application/json"}, body
    )
    dt = time.perf_counter() - t0
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {data[:300]!r}")
    r = json.loads(data)
    text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
    u = r.get("usageMetadata", {})
    return text, {
        "prompt_tokens": u.get("promptTokenCount"),
        "completion_tokens": u.get("candidatesTokenCount"),
    }, round(dt, 1)


def call_openrouter(png_b64: str, api_key: str):
    body = json.dumps(
        {
            "model": OPENROUTER_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{png_b64}"},
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
            "max_tokens": 6000,
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    t0 = time.perf_counter()
    r = json.load(urllib.request.urlopen(req, timeout=300))
    dt = time.perf_counter() - t0
    text = r["choices"][0]["message"]["content"]
    u = r.get("usage", {})
    return text, {
        "prompt_tokens": u.get("prompt_tokens"),
        "completion_tokens": u.get("completion_tokens"),
        "cost_usd": u.get("cost"),
    }, round(dt, 1)


def main():
    t = transport()
    if t == "relay":
        api_key = key_from(
            (("GEMINI_API_KEY",), "/opt/kestra/kestra.env"),
            (("GEMINI_API_KEY",), str(BASE.parent.parent / ".env")),
        )
    else:
        api_key = key_from(
            (("OPENROUTER_API_KEY",), str(BASE.parent.parent / ".env")),
        )
    if not api_key:
        raise SystemExit(f"API key not found for transport {t}")

    model = GOOGLE_MODEL if t == "relay" else OPENROUTER_MODEL
    out = out_dir()
    results_path = out / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}

    for png in sorted((BASE / "outputs" / "page_png").glob("*.png")):
        name = png.stem.rsplit("_p", 1)[0]
        if (out / f"{name}.md").exists():
            print(name, "skip (resume)", flush=True)
            continue
        png_b64 = base64.b64encode(png.read_bytes()).decode()
        try:
            call = call_relay if t == "relay" else call_openrouter
            text, usage, dt = call(png_b64, api_key)
        except Exception as e:
            print(name, "ERROR", e, flush=True)
            results[name] = {"transport": t, "error": str(e)[:200]}
            results_path.write_text(json.dumps(results, indent=2))
            continue
        (out / f"{name}.md").write_text(text)
        results[name] = {"transport": t, "model": model, "seconds": dt, **usage}
        results_path.write_text(json.dumps(results, indent=2))
        print(name, results[name], flush=True)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
