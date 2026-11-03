# Using a character image for the body map

The "You" page draws a holographic avatar in SVG by default. If you have a character
image, Mirror uses it as the body instead, and the stars stay on top of it.

## A character designed from the person's profile

Each person gets their own character, and it is built from their profile, not from a fixed
prompt. A running Mirror app is read through `/api/map`; for every body part the top
non-private things are collected (statements marked `internal_only` are never used), a
language model turns them into a short design (what the avatar wears or holds, the mood and
the palette), and Muse Image draws it.

```sh
META_API_KEY=... python -m mirror.avatar_gen --design --map-url http://HOST:8001 --out data/demo/avatar
# look at data/demo/avatar/candidates/, then install the one you like
python -m mirror.avatar_gen --input data/demo/avatar/candidates/avatar2.png --out data/demo/avatar \
    --parts '{"brain":[0.5,0.1],"eyes":[0.43,0.245],"ears":[0.77,0.26],"mouth":[0.54,0.29],"heart":[0.64,0.435],"hands":[0.88,0.475],"feet":[0.5,0.9]}'
python -m mirror.app --db data/demo/mirror.kuzu --avatar-dir data/demo/avatar --name "Persona A"
```

`design.json` in the output folder records the profile items that were sent and the design
that came back. `--parts` is needed because every picture is a little different; set it by
looking at where the headgear, glasses, headphones, smile, chest item, held item and shoes
are. `avatar.json` can also hold `"labels": {"heart": [1, -8, 1.0]}` (side, vertical offset,
distance) to move a label that collides with something in the picture.

Set `MIRROR_PERSONAS='[{"name": "A", "url": "http://HOST:8001"}, ...]'` to get a person
switcher in the sidebar.

## Fastest: Muse Image

Muse Image (Meta's image model, about $0.01 per image) is reachable at
`POST https://api.meta.ai/v1/images/generations` with the model `muse-image-1.0` and the
same bearer key as Muse Spark. This repo has a helper that generates candidates, cuts out
the green background and installs one with the right star positions:

```sh
MODEL_API_KEY=... python -m mirror.avatar_gen --generate --n 2      # writes avatar-candidates/
python -m mirror.avatar_gen --input avatar-candidates/avatar2.png    # installs the one you pick
```

The built-in prompt draws a person, not a robot, and ties each body part to something they
wear or hold: a lightbulb beanie (brain), round glasses (eyes), headphones (ears), a smile
(mouth), a glowing heart badge (heart), a notebook and pen (hands) and glowing sneakers
(feet). `PARTS` in `mirror/avatar_gen.py` says where each hub sits on that picture; for a
different picture pass `--parts '{"brain": [0.5, 0.07], ...}'` (fractions of the image).

The generated image and `avatar.json` are git-ignored, because the terms for publishing
generated images were not checked. Everyone who clones the repo sees the built-in avatar
until they generate their own.

## 1. Make the image yourself

Any image generator works. Aim for a transparent PNG (or WebP) of a single character,
front view, full body, standing straight with arms slightly away from the body, about
1:2 width to height (for example 1024 x 2048). A starting prompt:

> Full-body front view of a stylized metaverse avatar, neutral standing pose with arms
> slightly away from the body, glossy holographic material, soft neon rim light, rounded
> friendly proportions, smooth visor face, no background, transparent background, centered,
> high resolution

Any image-generation tool works.

## 2. Add the two files

Put these next to `app.html` in `mirror/static/`:

- `avatar.png` (or `.webp`, `.jpg`)
- `avatar.json`:

```json
{
  "image": "avatar.png",
  "x": 250, "y": 40, "w": 500, "h": 900,
  "anchors": {
    "brain": {"x": 500, "y": 112}, "eyes": {"x": 408, "y": 198}, "ears": {"x": 592, "y": 198},
    "mouth": {"x": 500, "y": 268}, "heart": {"x": 500, "y": 400},
    "hands": {"x": 316, "y": 566}, "feet": {"x": 500, "y": 884}
  }
}
```

The map is a 1000 x 1000 space. `x`, `y`, `w`, `h` place the image in it. `anchors` is
optional and moves each body part's star hub so it sits on the matching part of your
image. Reload the page after changing either file. Delete `avatar.json` to go back to the
built-in avatar.

Only `avatar.png`, `.webp`, `.jpg`, `.jpeg` and `avatar.json` are served from that folder.
