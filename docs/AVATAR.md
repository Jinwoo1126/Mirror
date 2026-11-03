# Using a character image for the body map

The "You" page draws a holographic avatar in SVG by default. If you have a character
image, Mirror uses it as the body instead, and the stars stay on top of it.

## 1. Make the image

Any image generator works. Aim for a transparent PNG (or WebP) of a single character,
front view, full body, standing straight with arms slightly away from the body, about
1:2 width to height (for example 1024 x 2048). A starting prompt:

> Full-body front view of a stylized metaverse avatar, neutral standing pose with arms
> slightly away from the body, glossy holographic material, soft neon rim light, rounded
> friendly proportions, smooth visor face, no background, transparent background, centered,
> high resolution

Muse can read images (`muse exec --image`) but cannot generate them, so use an
image-generation tool you already have.

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
