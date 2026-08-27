#!/usr/bin/env python3
"""Build the acceptance contact sheet (HU-2150 evidence artifact).

Grid: curated reference + the 3 acceptance outputs for identity A, each
labeled with its gate score vs threshold. Read side-by-side the measured
verdicts in acceptance/*.verdict.json — the sheet is presentation, the
scores are the proof.
"""

import glob
import json
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "gold", "ident-a", "reference.png")
CELL = 320
PAD = 8
LABEL_H = 34


def label_for(path):
    vf = path + ".verdict.json"
    if os.path.exists(vf):
        with open(vf) as f:
            v = json.load(f)
        return f"ACCEPT {os.path.basename(path)}  score={v['score']} (thr {v['threshold']})"
    return "REFERENCE (curated set)"


def main():
    outs = sorted(glob.glob(os.path.join(HERE, "acceptance", "accept-*.png")))
    panels = [REF] + outs
    W = len(panels) * (CELL + PAD) + PAD
    H = CELL + LABEL_H + 2 * PAD
    sheet = Image.new("RGB", (W, H), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(panels):
        im = Image.open(p).convert("RGB").resize((CELL, CELL))
        x = PAD + i * (CELL + PAD)
        sheet.paste(im, (x, PAD))
        d.text((x + 4, PAD + CELL + 10), label_for(p)[:52], fill=(230, 230, 230))
    out = os.path.join(HERE, "acceptance-contact-sheet.png")
    sheet.save(out)
    print(json.dumps({"ok": True, "sheet": out, "panels": len(panels)}))


if __name__ == "__main__":
    main()
