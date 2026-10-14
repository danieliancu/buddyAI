"""Generate components/ui/ui_logo_img.c: the ola logo of the website, for the watch's dark screens.

The same drawing as site/src/components/Logo.astro (light variant, without "companion"): the five-bar
wave (gradient #8b7bff -> #4ea3ff), then "ola" in white Inter Bold with the leaf (#a5b4fc) on the "a".
The leaf's vein, drawn in the page background on the website, is cut out (transparent) here, so it
reads on any dark background. Output: an LVGL RGB565A8 image (RGB565 plane, then the alpha plane).

Needs Pillow, fontTools and brotli (pip install pillow fonttools brotli) and the site's node_modules
(the Inter variable font of @fontsource-variable/inter).
Run:  python tools/gen_logo.py [--em 80]
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
FONT = ROOT / "site/node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2"
OUT = ROOT / "firmware/components/ui/ui_logo_img.c"

SS = 4                          # supersampling
GRAD = ((0x8B, 0x7B, 0xFF), (0x4E, 0xA3, 0xFF))
LEAF = (0xA5, 0xB4, 0xFC)
WHITE = (255, 255, 255)


def inter_bold(px: int) -> ImageFont.FreeTypeFont:
    font = TTFont(str(FONT))
    font.flavor = None
    bold = instancer.instantiateVariableFont(font, {"wght": 700})
    buf = io.BytesIO()
    bold.save(buf)
    buf.seek(0)
    return ImageFont.truetype(buf, px)


def bezier(p0, p1, p2, p3, n=48):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def render(em: int) -> Image.Image:
    """The logo at SS x the size, `em` = the text's font size in watch pixels (the website's text-xl
    20 px: icon 26 px = 1.3 em, gap-2 8 px = 0.4 em, tracking-tight -0.025 em)."""
    e = em * SS
    font = inter_bold(e)
    ascent, descent = font.getmetrics()
    icon = 1.3 * e
    gap = 0.4 * e
    track = -0.025 * e
    text_w = sum(font.getlength(c) for c in "ola") + 2 * track
    w = int(icon + gap + text_w + 0.1 * e)
    leaf_top = -0.1 * e                       # leaf: 2 px (0.1 em) above the "a"'s content box
    h = int(max(icon, ascent + descent - leaf_top)) + 2 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # text: content box (ascent + descent) centred, like the website's items-center
    text_top = (h - (ascent + descent)) / 2
    icon_top = (h - icon) / 2

    # icon: 32 x 32 viewBox, bars at x 4..28, round caps, stroke 3.2, gradient over x 4..28
    k = icon / 32
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    r = 1.6 * k
    for x, y0, y1 in ((4, 13, 19), (10, 9, 23), (16, 5, 27), (22, 9, 23), (28, 13, 19)):
        md.rounded_rectangle((x * k - r, icon_top + y0 * k - r, x * k + r, icon_top + y1 * k + r), radius=r, fill=255)
    grad = Image.new("RGBA", (w, h))
    gp = grad.load()
    for px in range(w):
        t = min(max((px / k - 4) / 24, 0.0), 1.0)
        c = tuple(round(GRAD[0][i] + (GRAD[1][i] - GRAD[0][i]) * t) for i in range(3)) + (255,)
        for py in range(h):
            gp[px, py] = c
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    x = icon + gap
    a_x = 0.0
    for ch in "ola":
        if ch == "a":
            a_x = x
        d.text((x, text_top), ch, font=font, fill=WHITE + (255,))
        x += font.getlength(ch) + track

    # leaf: 0.55 em square, 16 x 16 viewBox, left 2 px (0.1 em) into the "a"
    s = 0.55 * e / 16
    ox, oy = a_x + 0.1 * e, text_top + leaf_top
    tr = lambda p: (ox + p[0] * s, oy + p[1] * s)
    outline = bezier((2.5, 13.5), (2.5, 7), (6.8, 2.6), (14, 2)) + bezier((14, 2), (14.4, 9), (9.8, 13.5), (2.5, 13.5))
    d.polygon([tr(p) for p in outline], fill=LEAF + (255,))
    vein = Image.new("L", (w, h), 0)
    vd = ImageDraw.Draw(vein)
    vw = 1.1 * s
    a, b = tr((2.5, 13.5)), tr((9.5, 6.5))
    vd.line((a, b), fill=255, width=round(vw))
    for c in (a, b):
        vd.ellipse((c[0] - vw / 2, c[1] - vw / 2, c[0] + vw / 2, c[1] + vw / 2), fill=255)
    alpha = img.getchannel("A")
    alpha.paste(0, (0, 0), vein)
    img.putalpha(alpha)

    bbox = img.getbbox()
    return img.crop(bbox)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--em", type=int, default=80, help="font size of 'ola' in watch pixels")
    ap.add_argument("--png", type=Path, help="also save a preview PNG here")
    args = ap.parse_args()

    big = render(args.em)
    w, h = (big.width + SS - 1) // SS, (big.height + SS - 1) // SS
    img = big.resize((w, h), Image.LANCZOS)
    if args.png:
        img.save(args.png)

    rgb, alpha = [], []
    px = img.tobytes()
    for i in range(0, len(px), 4):
        r, g, b, a = px[i:i + 4]
        if a == 0:
            r = g = b = 0
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        rgb += [v & 0xFF, v >> 8]           # little-endian, as LVGL keeps RGB565 in memory
        alpha.append(a)

    def rows(data, per=24):
        return "\n".join("    " + ", ".join(f"0x{v:02X}" for v in data[i:i + per]) + ","
                         for i in range(0, len(data), per))

    OUT.write_text(f"""/*
 * ola - the logo of the website (wave icon + "ola" with the leaf), white text, for dark screens
 * (start-up splash, "Turning off..."). Generated by tools/gen_logo.py (--em {args.em}); do not edit.
 * {w} x {h} px, RGB565A8: the RGB565 plane (little-endian), then the 8-bit alpha plane.
 */
#include <stdint.h>
#include "ui_priv.h"

static const uint8_t s_logo_data[{len(rgb) + len(alpha)}] = {{
{rows(rgb)}
{rows(alpha)}
}};

const lv_image_dsc_t g_logo_img = {{
    .header = {{
        .magic = LV_IMAGE_HEADER_MAGIC,
        .cf = LV_COLOR_FORMAT_RGB565A8,
        .w = {w},
        .h = {h},
        .stride = {w * 2},
    }},
    .data_size = sizeof(s_logo_data),
    .data = s_logo_data,
}};
""", encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(ROOT)}: {w} x {h} px")


if __name__ == "__main__":
    main()
