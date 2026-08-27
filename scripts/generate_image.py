#!/usr/bin/env python3
"""
Kestra worker — Persona image generation (Option 4).

Generates persona avatar/scene imagery with FAL (FLUX) and writes the image
directly into the persona vault. API key from FAL_KEY env var.

Usage (legacy text-to-image — R&D synthetic-seed identities ONLY, see
docs/IDENTITY_IMAGE_PIPELINE.md — never for persona assets):
  python3 generate_image.py --prompt "..." --out /path/avatar.png

Usage (identity-conditioned, the persona path — HU-2150):
  python3 generate_image.py --prompt "same person, ..." \
      --ref-image /vault/references/raw/ref_x.jpg --out /path/scene.png

Uses FAL's REST queue API with polling (no SDK dependency):
  POST https://queue.fal.run/{model}                  (submit)
  GET  .../requests/{id}/status                       (poll)
  GET  .../requests/{id}                              (result)
"""

import argparse
import base64
import contextlib
import datetime
import hashlib
import json
import os
import sys
import time
import urllib.error
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


MODELS = {
    "flux-schnell": "https://queue.fal.run/fal-ai/flux/schnell",  # text-to-image
    "flux-pro-kontext": "https://queue.fal.run/fal-ai/flux-pro/kontext",  # identity-conditioned
    "flux-kontext-dev": "https://queue.fal.run/fal-ai/flux-kontext/dev",  # identity-conditioned
}


def req(method, url, key, body=None):
    headers = {"Authorization": f"Key {key}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def req_retry(method, url, key, body=None, attempts=5, delay=4):
    """req() with retry on transient queue errors.

    The FAL queue response endpoint can briefly return 4xx right after the
    status flips to COMPLETED (result not yet materialized); retrying with
    backoff turns those into successes instead of crashes.
    """
    last = None
    for i in range(attempts):
        try:
            return req(method, url, key, body)
        except urllib.error.HTTPError as e:
            with contextlib.suppress(Exception):  # best-effort error detail
                detail = e.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code} on {method} {url.split('/requests/')[0]}: {detail}"
            if e.code >= 500 or (e.code >= 400 and i < attempts - 1):
                time.sleep(delay * (i + 1))
                continue
            raise RuntimeError(last) from e
        except urllib.error.URLError as e:
            last = f"URLError on {method}: {e}"
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
                continue
            raise RuntimeError(last) from e
    raise RuntimeError(last or "req_retry exhausted")


def data_uri(path):
    """Local image -> base64 data URI (FAL accepts data URIs for image inputs)."""
    with open(path, "rb") as f:
        b = f.read()
    return "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--aspect", default="square")
    ap.add_argument("--model", default="flux-schnell", choices=sorted(MODELS))
    ap.add_argument("--ref-image", default=None,
                    help="reference photo for identity conditioning (Kontext models)")
    ap.add_argument("--strength", type=float, default=1.0,
                    help="Kontext change strength 0..1 (default 1.0)")
    args = ap.parse_args()

    if args.ref_image and args.model == "flux-schnell":
        print(json.dumps({"ok": False, "error": (
            "refused: text-to-image has no identity conditioning — use a Kontext "
            "model for persona assets (docs/IDENTITY_IMAGE_PIPELINE.md)")}))
        sys.exit(1)

    key = _key_from(("FAL_KEY",))
    if not key:
        print(json.dumps({"ok": False, "error": "FAL_KEY not set"}))
        sys.exit(1)

    t0 = time.time()
    try:
        if args.ref_image:
            body = {
                "prompt": args.prompt,
                "image_url": data_uri(args.ref_image),
                "strength": args.strength,
                "aspect_ratio": args.aspect if args.aspect != "square" else "1:1",
            }
        else:
            body = {"prompt": args.prompt, "image_size": args.aspect}
        submitted = req("POST", MODELS[args.model], key, body)
        status_url = submitted["status_url"]
        result_url = submitted["response_url"]
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"submit failed: {e}"}))
        sys.exit(1)

    timeout_s = 360 if args.ref_image else 240  # Kontext jobs run longer than schnell
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(3)
        try:
            st = req_retry("GET", status_url, key)
        except RuntimeError as e:
            print(json.dumps({"ok": False, "error": f"status poll failed: {e}"}))
            sys.exit(1)
        if st.get("status") == "COMPLETED":
            break
        if st.get("status") == "FAILED":
            print(json.dumps({"ok": False, "error": f"FAL job failed: {json.dumps(st)[:300]}"}))
            sys.exit(1)
    else:
        print(json.dumps({"ok": False, "error": f"FAL polling timed out after {timeout_s}s"}))
        sys.exit(1)

    try:
        result = req_retry("GET", result_url, key)
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": f"result fetch failed: {e}"}))
        sys.exit(1)
    image_url = (result.get("images") or [{}])[0].get("url")
    if not image_url:
        err = f"no image URL in result: {json.dumps(result)[:300]}"
        print(json.dumps({"ok": False, "error": err}))
        sys.exit(1)

    blob = None
    for i in range(4):  # CDN fetch can also blip transiently
        try:
            with urllib.request.urlopen(image_url, timeout=120) as resp:
                blob = resp.read()
            break
        except Exception as e:
            if i == 3:
                print(json.dumps({"ok": False, "error": f"image download failed: {e}"}))
                sys.exit(1)
            time.sleep(4 * (i + 1))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(blob)

    size = os.path.getsize(args.out)
    if size < 5000:
        print(json.dumps({"ok": False, "error": f"image too small ({size}B)"}))
        sys.exit(1)

    # Provenance sidecar (input to the identity registry — HU-2150 REGISTRY stage)
    prov = {
        "out": os.path.abspath(args.out),
        "out_sha256": sha256_of(args.out),
        "ref_image": os.path.abspath(args.ref_image) if args.ref_image else None,
        "ref_sha256": sha256_of(args.ref_image) if args.ref_image else None,
        "prompt": args.prompt,
        "model": args.model,
        "aspect": args.aspect,
        "strength": args.strength if args.ref_image else None,
        "bytes": size,
        "latency_s": round(time.time() - t0, 2),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    with open(args.out + ".prov.json", "w", encoding="utf-8") as f:
        json.dump(prov, f, indent=2)

    print(json.dumps({"ok": True, "out": args.out, "bytes": size,
                      "engine": MODELS[args.model], "prov": args.out + ".prov.json"}))


if __name__ == "__main__":
    main()
