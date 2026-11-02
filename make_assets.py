"""Build the game's picture files into assets/ from raw_art/.

Run this on your computer:  python make_assets.py
main.py never imports this file (it would break in a browser); it only
loads the finished PNGs from assets/. The originals in raw_art/ are only
read, never changed.

What it does:
- scales every sprite 3x with nearest-neighbour, so the pixels stay crisp
  and nothing is blurred;
- derives two bright crystal gems from raw_art/gem.png by replacing their
  hue: blue for a gem worth 1 XP, pink for a gem worth 2 XP;
- writes 48 x 48 RGBA PNGs into assets/ (recreated on every run).
"""

import colorsys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw_art"
OUT = ROOT / "assets"

GEM_BASE = "gem.png"
GEM_TINTS = {
    # out name -> (hue 0..1, minimum saturation, minimum brightness)
    "gem_blue.png": {"hue": 0.60, "sat": 0.95, "val": 1.00},  # for XP 1
    "gem_pink.png": {"hue": 0.90, "sat": 0.90, "val": 1.00},  # for XP 2
}


def scale3(im):
    w, h = im.size
    return im.resize((w * 3, h * 3), Image.Resampling.NEAREST)


def tint_gem(im, tint):
    """Keep the gem's lightness pattern, but make it a bright hue."""
    rgba = im.convert("RGBA")
    new = Image.new("RGBA", rgba.size)
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = rgba.getpixel((x, y))
            if a == 0:
                new.putpixel((x, y), (0, 0, 0, 0))
                continue
            _, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            rgb = colorsys.hsv_to_rgb(tint["hue"], max(s, tint["sat"]), max(v, tint["val"]))
            new.putpixel((x, y), tuple(round(c * 255) for c in rgb) + (a,))
    return new


def main():
    if OUT.exists():
        for old in OUT.glob("*.png"):
            old.unlink()
    else:
        OUT.mkdir()

    sprites = sorted(p for p in RAW.glob("*.png") if p.name != GEM_BASE)
    for path in sprites:
        with Image.open(path) as src:
            scale3(src).save(OUT / path.name)
        print("scaled", path.name)

    gem = RAW / GEM_BASE
    if gem.exists():
        with Image.open(gem) as base:
            for out_name, tint in GEM_TINTS.items():
                scale3(tint_gem(base, tint)).save(OUT / out_name)
                print("tinted", out_name)

    total = len(list(OUT.glob("*.png")))
    print(f"assets/ now has {total} PNGs")


if __name__ == "__main__":
    main()
