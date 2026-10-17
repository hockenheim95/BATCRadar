"""Render BATCRadar brand assets (not imported by the app at runtime).

Plane silhouette matches web/js/01-icons.js default twin-jet shape.
"""
from __future__ import annotations

import os

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))

BG_WHITE = (255, 255, 255, 255)
CYAN = (0, 168, 232, 255)
INK = (20, 22, 28, 255)

MAP_TWIN_PATH = (
    "M40 4c2 0 3.3 2 3.3 6.2V18L76 33.5v5.4L43.3 28.2V50l16 12v5.2L40 59.2 "
    "20.7 67.4v-5.2l16-12V28.2L4 38.9v-5.4L36.7 18V10.2C36.7 6 38 4 40 4z"
)
MAP_TWIN_ENGINES = (
    '<ellipse cx="20" cy="34" rx="3.4" ry="7.2"/>'
    '<ellipse cx="60" cy="34" rx="3.4" ry="7.2"/>'
)


def rounded_rect_mask(size, radius):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m


def _rgb_hex(rgb):
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def render_map_plane(size: int, fill_rgb=INK) -> Image.Image:
    fill = _rgb_hex(fill_rgb)
    stroke_w = 2.2
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80" width="{size}" height="{size}">
  <g fill="{fill}" stroke="#111111" stroke-width="{stroke_w}"
     stroke-linejoin="round" stroke-linecap="round" paint-order="stroke fill">
    <path d="{MAP_TWIN_PATH}"/>
    {MAP_TWIN_ENGINES}
  </g>
</svg>"""
    doc = fitz.open(stream=svg.encode("utf-8"), filetype="svg")
    page = doc[0]
    pix = page.get_pixmap(alpha=True)
    doc.close()
    img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def draw_radar_arcs(draw, cx, cy, radii, width, color, gap_deg=58, gap_center=40):
    for r in radii:
        bbox = [cx - r, cy - r, cx + r, cy + r]
        a0 = gap_center + gap_deg / 2
        a1 = gap_center - gap_deg / 2 + 360
        draw.arc(bbox, start=a0, end=a1, fill=color, width=width)


def render_mark(size, plane_color=INK, pad_ratio=0.04):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    outer = size * (0.5 - pad_ratio)
    radii = [outer * 0.72, outer]
    stroke = max(3, int(size * 0.05))
    draw_radar_arcs(draw, cx, cy, radii, stroke, CYAN)
    plane_px = int(outer * 1.05)
    plane = render_map_plane(plane_px, plane_color)
    px = int(cx - plane_px / 2)
    py = int(cy - plane_px / 2)
    img.alpha_composite(plane, (px, py))
    return img


def write_ico(png: Image.Image, path: str):
    """Multi-size Windows icon from a square image."""
    base = png.convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(path, format="ICO", sizes=sizes)


def main():
    os.makedirs(OUT, exist_ok=True)

    # --- EXE icon: white fill, rounded corners (transparent outside), large mark ---
    S = 1024
    radius = int(S * 0.22)
    mask = rounded_rect_mask((S, S), radius=radius)
    exe = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    white = Image.new("RGBA", (S, S), BG_WHITE)
    exe = Image.composite(white, exe, mask)
    raw = render_mark(900, INK, pad_ratio=0.04)
    bb = raw.split()[-1].getbbox()
    cropped = raw.crop(bb)
    target = int(S * 0.90)
    scale = target / max(cropped.size)
    new_w = max(1, int(cropped.size[0] * scale))
    new_h = max(1, int(cropped.size[1] * scale))
    mark = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
    ox = (S - mark.size[0]) // 2
    oy = (S - mark.size[1]) // 2
    exe.alpha_composite(mark, (ox, oy))
    # Keep corners transparent (clip content to rounded rect)
    r, g, b, a = exe.split()
    a = Image.composite(a, Image.new("L", (S, S), 0), mask)
    exe = Image.merge("RGBA", (r, g, b, a))

    exe_png = os.path.join(OUT, "batcradar-exe.png")
    exe.save(exe_png)
    print("wrote", exe_png)

    ico_path = os.path.join(OUT, "batcradar.ico")
    write_ico(exe, ico_path)
    print("wrote", ico_path)

    # --- Flightsim lockup: transparent background, no frame ---
    big = render_mark(800, INK, pad_ratio=0.08)
    bbox = big.split()[-1].getbbox()
    mark = big.crop(bbox)
    pad = 8
    mark2 = Image.new("RGBA", (mark.size[0] + pad * 2, mark.size[1] + pad * 2), (0, 0, 0, 0))
    mark2.alpha_composite(mark, (pad, pad))
    mark = mark2
    mark_h = mark.size[1]
    mark_w = mark.size[0]

    font_paths = [
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\seguisb.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
    ]
    font_path = next(p for p in font_paths if os.path.exists(p))
    text = "BATCRadar"
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    lo, hi, best = 10, 900, 40
    for _ in range(30):
        mid = (lo + hi) // 2
        font = ImageFont.truetype(font_path, mid)
        bb = probe.textbbox((0, 0), text, font=font)
        th = bb[3] - bb[1]
        if th <= mark_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    font = ImageFont.truetype(font_path, best)
    bb = probe.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    while th > mark_h and best > 10:
        best -= 1
        font = ImageFont.truetype(font_path, best)
        bb = probe.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]

    gap = int(mark_h * 0.14)
    # tight transparent padding around mark + text only
    inner = int(mark_h * 0.06)
    W = mark_w + gap + tw + inner * 2
    H = mark_h + inner * 2
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    mx, my = inner, inner
    canvas.alpha_composite(mark, (mx, my))
    tx = mx + mark_w + gap
    ty = my + (mark_h - th) // 2 - bb[1]
    draw.text((tx, ty), text, font=font, fill=INK)

    fs_path = os.path.join(OUT, "batcradar-flightsimto.png")
    canvas.save(fs_path)
    print("wrote", fs_path, f"{W}x{H}", "font", best)


if __name__ == "__main__":
    main()
