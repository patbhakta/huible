#!/usr/bin/env python3
"""
Kestra worker — Persona image generation (Option 4).

Generates persona avatar/scene imagery with FAL (FLUX) and writes the image
directly into the persona vault. API key from FAL_KEY env var.

Usage:
  python3 generate_image.py --prompt "..." --out /path/avatar.png

Uses FAL's REST queue API with polling (no SDK dependency):
  POST https://queue.fal.run/fal-ai/flux/schnell  (submit)
  GET  .../requests/{id}/status                   (poll)
  GET  .../requests/{id}                          (result)
"""

import argparse
import json
import os
import sys
import time
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


SUBMIT_URL = "https://queue.fal.run/fal-ai/flux/schnell"


def req(method, url, key, body=None):
    headers = {"Authorization": f"Key {key}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--aspect", default="square")
    args = ap.parse_args()

    key = _key_from(("FAL_KEY",))
    if not key:
        print(json.dumps({"ok": False, "error": "FAL_KEY not set"}))
        sys.exit(1)

    try:
        submitted = req("POST", SUBMIT_URL, key, {
            "prompt": args.prompt,
            "image_size": args.aspect,
        })
        status_url = submitted["status_url"]
        result_url = submitted["response_url"]
    except Exception as e:  # noqa: BLE001 — surface any submit failure to Kestra
        print(json.dumps({"ok": False, "error": f"submit failed: {e}"}))
        sys.exit(1)

    deadline = time.time() + 240
    while time.time() < deadline:
        time.sleep(3)
        st = req("GET", status_url, key)
        if st.get("status") == "COMPLETED":
            break
        if st.get("status") == "FAILED":
            print(json.dumps({"ok": False, "error": f"FAL job failed: {json.dumps(st)[:300]}"}))
            sys.exit(1)
    else:
        print(json.dumps({"ok": False, "error": "FAL polling timed out after 240s"}))
        sys.exit(1)

    result = req("GET", result_url, key)
    image_url = (result.get("images") or [{}])[0].get("url")
    if not image_url:
        print(json.dumps({"ok": False, "error": f"no image URL in result: {json.dumps(result)[:300]}"}))
        sys.exit(1)

    with urllib.request.urlopen(image_url, timeout=120) as resp:
        blob = resp.read()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(blob)

    size = os.path.getsize(args.out)
    if size < 5000:
        print(json.dumps({"ok": False, "error": f"image too small ({size}B)"}))
        sys.exit(1)
    print(json.dumps({"ok": True, "out": args.out, "bytes": size, "engine": "fal-ai/flux/schnell"}))


if __name__ == "__main__":
    main()
