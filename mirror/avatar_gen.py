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
    "Full-body front view of a stylized metaverse avatar character standing in a neutral "
    "pose with arms slightly away from the body, glossy translucent holographic material "
    "in deep indigo and violet with soft cyan neon rim light, rounded friendly proportions, "
    "smooth glossy visor face with two small glowing eyes, a subtle glowing core on the "
    "chest, clean polished 3D render look, centered with the entire body visible from head "
    "to feet and a small margin around it, on a perfectly flat solid pure green (#00FF00) "
    "background, no floor, no shadow, no text")

# Where the map is drawn (a 1000 x 1000 space) and where each part sits on a front-view
# character, as fractions of the image (u across, v down).
RECT = {"x": 275, "y": 40, "w": 450, "h": 900}
PARTS = {"brain": (0.50, 0.095), "eyes": (0.40, 0.2325), "ears": (0.77, 0.215),
         "mouth": (0.50, 0.325), "heart": (0.50, 0.4525), "hands": (0.15, 0.64),
         "feet": (0.50, 0.95)}


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
    ap.add_argument("--out", default=str(Path(__file__).parent / "static"))
    args = ap.parse_args(argv)
    if args.generate:
        d = Path(args.candidates)
        d.mkdir(exist_ok=True)
        for i, png in enumerate(generate(args.n), 1):
            (d / f"avatar{i}.png").write_bytes(png)
            print("saved", d / f"avatar{i}.png")
    if args.input:
        print("installed", install(Path(args.input), Path(args.out)))
    if not args.generate and not args.input:
        ap.error("use --generate and/or --input")


if __name__ == "__main__":
    main()
