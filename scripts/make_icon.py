"""SVG -> ICO/PNG for the WUXICODE executable.

The SVG in `assets/logo.svg` is the source of truth. Two renderers are tried:

    1. cairosvg   — rasterises the SVG exactly as written.
    2. Pillow     — redraws the same artwork in code.

cairosvg is the preferred path, but on Windows it needs a native `libcairo`
that pip cannot install, and a missing DLL surfaces as an ImportError from
cairocffi at import time. Rather than make the build depend on a system
library, the fallback exists so `python scripts/make_icon.py` works on a clean
machine. Both paths emit the same geometry: 256px viewBox, #7C3AED -> #06B6D4
diagonal gradient, rx=48 rounded square, white 14px-stroke WX, white glow.

Every ICO entry is rendered natively at its own pixel size rather than
downscaled from 1024px. A 16px icon downscaled from 1024 loses the counters and
the W crotch to grey mush; drawn at 16px with 4x supersampling it stays crisp.

    python scripts/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    sys.exit("install first: pip install pillow")

# The Korean Windows console defaults to cp949, which cannot encode the status
# marks below. Reconfigure rather than downgrading them to ASCII.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

try:
    import cairosvg

    HAVE_CAIROSVG = True
except Exception:  # ImportError, or cairocffi failing to load libcairo
    HAVE_CAIROSVG = False

# ICO entries. 256 is the largest Windows will store in an .ico.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZES = (512, 1024)

# --- artwork constants, mirroring assets/logo.svg ---------------------------
VIEW = 256
BOX = 16  # rounded square inset
RADIUS = 48
STROKE_W = 14
GRAD_FROM = (0x7C, 0x3A, 0xED)  # #7C3AED violet
GRAD_TO = (0x06, 0xB6, 0xD4)  # #06B6D4 cyan
EDGE_FROM = (0xA7, 0x8B, 0xFA)  # #A78BFA
EDGE_TO = (0x22, 0xD3, 0xEE)  # #22D3EE
GLOW_SIGMA = 3.0

# Glyph geometry, in viewBox units. Kept in sync with assets/logo.svg by hand;
# the W/X split leaves a 32-unit gutter so the 14-unit strokes do not merge.
W_PATH = ((44, 88), (66, 168), (88, 120), (110, 168), (132, 88))
X_PATH = ((164, 88), (212, 168))
X_PATH2 = ((212, 88), (164, 168))
DOTS = ((128, 200, 6.0, 0.7), (112, 200, 3.0, 0.5), (144, 200, 3.0, 0.5))

SS = 4  # supersample factor for the Pillow fallback


def _tuned(size: int) -> tuple[float, float, bool]:
    """(stroke_scale, dot_scale, keep_dots) for a target pixel size.

    14 viewBox units is 14/256 of the canvas, so at 16px every stroke lands
    under one pixel wide and the counters fill in — the glyph turns to mush.
    Below 32px the stroke is fattened and the decorative dots are dropped;
    they are the first thing to disappear at small sizes and they are the only
    element that carries no information.
    """
    if size <= 16:
        return 1.75, 0.0, False
    if size <= 24:
        return 1.45, 0.0, False
    if size <= 32:
        return 1.3, 0.8, True
    return 1.0, 1.0, True


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _diagonal_gradient(size: int, c_from, c_to, reverse: bool = False) -> Image.Image:
    """Linear gradient along the main diagonal, built small then upscaled.

    Rendered at 128px and resized: a gradient has no high-frequency detail, so
    this is visually identical to per-pixel math at 1024px and ~60x cheaper.
    """
    small = 128
    grad = Image.new("RGB", (small, small))
    px = grad.load()
    denom = float(2 * (small - 1))
    for y in range(small):
        for x in range(small):
            t = (x + y) / denom
            if reverse:
                t = 1.0 - t
            px[x, y] = _lerp(c_from, c_to, t)
    return grad.resize((size, size), Image.Resampling.BICUBIC)


def _stroke(draw: ImageDraw.ImageDraw, points, width: int, fill) -> None:
    """Polyline with round joins and round caps.

    Pillow's `joint="curve"` rounds the interior joins but leaves butt caps, so
    the caps are stamped as circles by hand.
    """
    pts = [(round(x), round(y)) for x, y in points]
    if len(pts) > 1:
        draw.line(pts, fill=fill, width=width, joint="curve")
    r = width / 2.0
    for x, y in pts:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def render_pillow(size: int) -> Image.Image:
    """Draw the logo at `size` px using Pillow only. No SVG rasteriser needed."""
    n = size * SS
    k = n / VIEW  # viewBox unit -> device pixel
    stroke_scale, dot_scale, keep_dots = _tuned(size)
    box = tuple(round(v * k) for v in (BOX, BOX, VIEW - BOX, VIEW - BOX))
    radius = round(RADIUS * k)
    # `max(1, ...)` is what makes the tuning work at all: at 16px a stroke is
    # 14/256 * 64 = 3.5px in the supersampled canvas, which rounds to 3 without
    # it and the counters close up.
    stroke = max(1, round(STROKE_W * k * stroke_scale))
    # The glow is a legibility aid; at 16px it is just blur that eats the
    # counters, so it fades out with the rest of the small-size tuning.
    glow_sigma = GLOW_SIGMA * k * (0.0 if size <= 16 else 0.6 if size <= 32 else 1.0)

    # Background gradient, clipped to the rounded square.
    bg = _diagonal_gradient(n, GRAD_FROM, GRAD_TO)
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(bg, (0, 0), mask)

    # Inner highlight stroke, opposite diagonal gradient at 50% opacity.
    edge = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    edge.paste(_diagonal_gradient(n, EDGE_FROM, EDGE_TO, reverse=True), (0, 0))
    edge_mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(edge_mask).rounded_rectangle(
        box, radius=radius, outline=round(2 * k), fill=0
    )
    edge.putalpha(edge_mask)
    edge.putalpha(edge.getchannel("A").point(lambda v: round(v * 0.5)))
    img.alpha_composite(edge)

    # Glyphs on their own layer, so the glow can be blurred independently.
    glyph = Image.new("L", (n, n), 0)
    gd = ImageDraw.Draw(glyph)
    w_pts = tuple((x * k, y * k) for x, y in W_PATH)
    _stroke(gd, w_pts, stroke, 255)
    _stroke(gd, tuple((x * k, y * k) for x, y in X_PATH), stroke, 255)
    _stroke(gd, tuple((x * k, y * k) for x, y in X_PATH2), stroke, 255)

    # Compositing a blurred copy of the glyph under itself is a legibility win
    # at 48px and up. At 16px the blur radius rounds to nothing, so the "glow"
    # becomes a soft 0.85-alpha halo sitting on top of the counters and turns
    # the whole mark to mush — skip it entirely rather than fake it.
    if glow_sigma > 0.5:
        glow = glyph.filter(ImageFilter.GaussianBlur(radius=glow_sigma))
        glow = Image.eval(glow, lambda v: round(v * 0.85))
        white = Image.new("RGBA", (n, n), (255, 255, 255, 0))
        white.putalpha(glow)
        img.alpha_composite(white)

    glyph_rgba = Image.new("RGBA", (n, n), (255, 255, 255, 0))
    glyph_rgba.putalpha(glyph)
    img.alpha_composite(glyph_rgba)

    # Decorative dots. Dropped entirely at 16/24px: sub-pixel circles there
    # only add grey speckle where the corners already have enough going on.
    if keep_dots:
        for cx, cy, r, opacity in DOTS:
            x, y, rr = cx * k, cy * k, r * k * dot_scale
            if rr < 0.5:
                continue
            dot = Image.new("L", (n, n), 0)
            ImageDraw.Draw(dot).ellipse((x - rr, y - rr, x + rr, y + rr), fill=255)
            alpha = round(opacity * 255)
            dot = dot.point(lambda v, a=alpha: round(v * a / 255))
        layer = Image.new("RGBA", (n, n), (255, 255, 255, 0))
        layer.putalpha(dot)
        img.alpha_composite(layer)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def render_cairosvg(svg_path: Path, size: int) -> Image.Image:
    """Rasterise the SVG with cairosvg."""
    import io

    png = cairosvg.svg2png(
        url=str(svg_path), output_width=size, output_height=size
    )
    return Image.open(io.BytesIO(png)).convert("RGBA")


def make_png(svg_path: Path, png_path: Path, size: int) -> Image.Image:
    """Render one PNG at `size` and write it. Returns the image."""
    if HAVE_CAIROSVG:
        try:
            img = render_cairosvg(svg_path, size)
        except Exception as exc:
            print(f"  ! cairosvg 실패 ({exc.__class__.__name__}), Pillow로 전환")
            img = render_pillow(size)
    else:
        img = render_pillow(size)
    img.save(png_path, format="PNG", optimize=True)
    return img


def make_ico(svg_path: Path, ico_path: Path) -> None:
    """Multi-size ICO, each entry rendered natively at its own size."""
    frames = [make_png(svg_path, svg_path.parent / f"._ico_{s}.png", s) for s in ICO_SIZES]
    base = frames[-1]  # 256, the entry Pillow treats as the source image
    rest = frames[:-1]
    base.save(ico_path, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
              append_images=rest)
    for s in ICO_SIZES:  # tidy up the per-size scratch files
        (svg_path.parent / f"._ico_{s}.png").unlink(missing_ok=True)


def verify_ico(ico_path: Path, expect: tuple[int, ...]) -> None:
    """Reopen the .ico and assert every expected size is really present."""
    with Image.open(ico_path) as ico:
        found = {s[0] for s in ico.ico.sizes()}
        missing = set(expect) - found
        if missing:
            sys.exit(f"✗ ICO 크기 누락: {sorted(missing)} (있는 것: {sorted(found)})")
        print(f"  ICO 엔트리 {sorted(found)}")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    svg = root / "assets" / "logo.svg"
    assets = root / "assets"

    if not svg.exists():
        print(f"✗ 없음: {svg}")
        return 1
    assets.mkdir(exist_ok=True)

    renderer = "cairosvg" if HAVE_CAIROSVG else "pillow"
    print(f"러스터라이저: {renderer}")

    for size in PNG_SIZES:
        out = assets / f"icon-{size}.png"
        make_png(svg, out, size)
        print(f"✓ {out.relative_to(root)}  ({out.stat().st_size // 1024} KB)")

    ico = assets / "icon.ico"
    make_ico(svg, ico)
    print(f"✓ {ico.relative_to(root)}  ({ico.stat().st_size // 1024} KB)")
    verify_ico(ico, ICO_SIZES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
