"""Make the body-map character with Muse Image, or prepare an image you already have.

  # generate candidates (about $0.01 each) into ./avatar-candidates/
  MODEL_API_KEY=... python -m mirror.avatar_gen --generate --n 2
  # cut out the green background and install one as the map's body
  python -m mirror.avatar_gen --input avatar-candidates/avatar2.png

The image is generated on a flat pure-green background, which is keyed out here, so the
result has a transparent background. Installing writes mirror/static/avatar.webp and
avatar.json (including where each body part's star hub sits on the image).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

ENDPOINT = "https://api.meta.ai/v1/images/generations"
MODEL = "muse-image-1.0"
PROMPT = (
    "Full-body front view of a friendly stylized 3D avatar of a young person, gender-neutral, "
    "in the polished style of a modern social metaverse app, standing in a relaxed pose, "
    "rounded friendly proportions, smooth skin, expressive happy face with a small smile, "
    "soft cyan and violet neon rim light. Wearing a cozy indigo hoodie with a small glowing "
    "pink heart badge on the chest, big over-ear headphones, round glasses, a beanie with a "
    "tiny glowing lightbulb on top, and white sneakers with a cyan glow. The left hand holds "
    "an open notebook and a pen, the right hand holds a steaming coffee cup. Both hands "
    "clearly visible and slightly away from the body. Centered, the entire body visible from "
    "head to feet with a small margin, on a perfectly flat solid pure green (#00FF00) "
    "background, no green clothing, no floor, no shadow, no text")

# Where the map is drawn (a 1000 x 1000 space) and where each part sits on the picture,
# as fractions of the image (u across, v down).
RECT = {"x": 275, "y": 40, "w": 450, "h": 900}
# Each part is tied to something the character wears or holds: lightbulb beanie (brain),
# glasses (eyes), headphones (ears), smile (mouth), heart badge (heart), notebook and pen
# (hands), sneakers (feet). Change these when you use a different picture.
PARTS = {"brain": (0.50, 0.075), "eyes": (0.42, 0.235), "ears": (0.725, 0.24),
         "mouth": (0.51, 0.286), "heart": (0.59, 0.3975), "hands": (0.23, 0.495),
         "feet": (0.50, 0.895)}


def generate(n: int = 1, prompt: str = PROMPT, size: str = "1024x2048") -> list[bytes]:
    key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY")
    if not key:
        raise SystemExit("Set MODEL_API_KEY (or META_API_KEY) to generate with Muse Image.")
    r = httpx.post(ENDPOINT, headers={"Authorization": f"Bearer {key}"}, timeout=240,
                   json={"model": MODEL, "prompt": prompt, "n": n, "size": size,
                         "response_format": "b64_json", "output_format": "png"})
    r.raise_for_status()
    return [base64.b64decode(d["b64_json"]) for d in r.json()["data"]]


def key_out_green(img: Image.Image, lo: float = 50.0, hi: float = 190.0) -> Image.Image:
    """Turn a flat green background transparent and remove the green tint from edges."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    greenness = g - np.maximum(r, b)                     # about 255 on the background
    alpha = 1.0 - np.clip((greenness - lo) / (hi - lo), 0.0, 1.0)
    g = np.minimum(g, np.maximum(r, b))                  # despill: green never leads
    out = np.dstack([r, g, b, alpha * 255.0]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def anchors() -> dict:
    return {k: {"x": round(RECT["x"] + u * RECT["w"]), "y": round(RECT["y"] + v * RECT["h"])}
            for k, (u, v) in PARTS.items()}


def install(src: Path, out_dir: Path, width: int = 840) -> Path:
    img = key_out_green(Image.open(src))
    img = img.resize((width, int(img.height * width / img.width)), Image.LANCZOS)
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / "avatar.webp", quality=90, alpha_quality=100, method=6)
    (out_dir / "avatar.json").write_text(json.dumps(
        {"image": "avatar.webp", **RECT, "anchors": anchors()}, indent=2))
    return out_dir / "avatar.webp"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Muse Image avatar for the body map")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--input", help="a green-background image to cut out and install")
    ap.add_argument("--candidates", default="avatar-candidates")
    ap.add_argument("--parts", help='JSON like {"brain": [0.5, 0.07], ...} (fractions of the image)')
    ap.add_argument("--out", default=str(Path(__file__).parent / "static"))
    args = ap.parse_args(argv)
    if args.generate:
        d = Path(args.candidates)
        d.mkdir(exist_ok=True)
        for i, png in enumerate(generate(args.n), 1):
            (d / f"avatar{i}.png").write_bytes(png)
            print("saved", d / f"avatar{i}.png")
    if args.parts:
        PARTS.update({k: tuple(v) for k, v in json.loads(args.parts).items()})
    if args.input:
        print("installed", install(Path(args.input), Path(args.out)))
    if not args.generate and not args.input:
        ap.error("use --generate and/or --input")


if __name__ == "__main__":
    main()
