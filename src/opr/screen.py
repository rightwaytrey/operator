"""Screen capture for the target X display.

Primary path is XGetImage via python-xlib (no external binaries, works on any
region). Because a single X request is capped (~16MB here), full-screen grabs
are read back in horizontal strips. ffmpeg's x11grab is the fallback if the
Xlib path fails for any reason.
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from . import xconn

SHOTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "shots")

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _capture_xlib(x: int, y: int, w: int, h: int) -> Image.Image:
    from Xlib import X

    d = xconn.display()
    root = xconn.root()
    # Stay well under the server's maximum request size (in 4-byte units).
    max_bytes = d.info.max_request_length * 4 - 4096
    rows = max(1, min(h, max_bytes // max(1, w * 4)))

    strips = []
    for top in range(0, h, rows):
        band = min(rows, h - top)
        raw = root.get_image(x, y + top, w, band, X.ZPixmap, 0xFFFFFFFF)
        data = raw.data
        if isinstance(data, str):  # older python-xlib returns str
            data = data.encode("latin-1")
        strips.append(Image.frombytes("RGB", (w, band), bytes(data), "raw", "BGRX"))

    if len(strips) == 1:
        return strips[0]
    out = Image.new("RGB", (w, h))
    off = 0
    for s in strips:
        out.paste(s, (0, off))
        off += s.height
    return out


def _capture_ffmpeg(x: int, y: int, w: int, h: int) -> Image.Image:
    disp = os.environ.get("DISPLAY") or xconn.default_display()
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "x11grab", "-video_size", f"{w}x{h}",
        "-i", f"{disp}+{x},{y}", "-frames:v", "1",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    png = subprocess.run(cmd, capture_output=True, check=True).stdout
    import io
    return Image.open(io.BytesIO(png)).convert("RGB")


def capture(region=None) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Grab a region (x, y, w, h) or the whole screen. Returns (image, region)."""
    if region is None:
        g = xconn.screen_geometry()
        region = (0, 0, g.width, g.height)
    x, y, w, h = region
    try:
        img = _capture_xlib(x, y, w, h)
    except Exception:
        img = _capture_ffmpeg(x, y, w, h)
    return img, (x, y, w, h)


def _grid_step(span: int) -> int:
    """Pick a round gridline spacing giving roughly 6-12 lines across."""
    target = span / 9
    for step in (25, 50, 100, 200, 250, 500, 1000, 2000):
        if step >= target:
            return step
    return 5000


def draw_grid(img: Image.Image, origin: tuple[int, int], scale: float) -> Image.Image:
    """Overlay labelled gridlines showing TRUE screen coordinates.

    Labels are the coordinates to pass to `op click`, regardless of the scale
    the image was saved at.
    """
    ox, oy = origin
    true_w = int(img.width / scale)
    true_h = int(img.height / scale)
    step_x = _grid_step(true_w)
    step_y = _grid_step(true_h)

    overlay = img.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    font = _font(max(11, int(13 * min(1.0, max(scale, 0.5)))))

    def label(px, py, text):
        bbox = draw.textbbox((px, py), text, font=font)
        draw.rectangle([bbox[0] - 2, bbox[1] - 1, bbox[2] + 2, bbox[3] + 1],
                       fill=(0, 0, 0, 190))
        draw.text((px, py), text, fill=(0, 255, 140, 255), font=font)

    for tx in range(ox - (ox % step_x) + step_x, ox + true_w, step_x):
        px = int((tx - ox) * scale)
        draw.line([(px, 0), (px, overlay.height)], fill=(255, 0, 90, 90), width=1)
        label(px + 3, 2, str(tx))

    for ty in range(oy - (oy % step_y) + step_y, oy + true_h, step_y):
        py = int((ty - oy) * scale)
        draw.line([(0, py), (overlay.width, py)], fill=(255, 0, 90, 90), width=1)
        label(3, py + 2, str(ty))

    return overlay


KEEP_SHOTS = 200


def _prune_shots(keep: int = KEEP_SHOTS) -> None:
    """Trim the auto-named screenshot backlog so it cannot grow without bound.

    Only touches shot-*.png / web-*.png files this tool generated in its own
    shots directory; anything saved with an explicit --out is left alone.
    """
    try:
        shots = sorted(
            (f for f in os.scandir(SHOTS_DIR)
             if f.is_file() and f.name.endswith(".png")
             and (f.name.startswith("shot-") or f.name.startswith("web-"))),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for stale in shots[keep:]:
            os.unlink(stale.path)
    except Exception:
        pass


def shot(region=None, scale: float = 1.0, grid: bool = False,
         out: str | None = None) -> dict:
    """Capture, optionally scale and grid-annotate, write a PNG, return metadata."""
    t0 = time.time()
    img, (x, y, w, h) = capture(region)

    if scale != 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                         Image.LANCZOS)
    if grid:
        img = draw_grid(img, (x, y), scale)

    if out is None:
        os.makedirs(SHOTS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        from . import session
        tag = session.current()["name"]
        out = os.path.join(SHOTS_DIR,
                           f"shot-{tag + '-' if tag else ''}{stamp}.png")
        _prune_shots()
    else:
        out = os.path.abspath(out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img.save(out, "PNG", optimize=True)

    g = xconn.screen_geometry()
    return {
        "path": out,
        "screen": f"{g.width}x{g.height}",
        "region": f"{x},{y},{w},{h}",
        "saved": f"{img.width}x{img.height}",
        "scale": scale,
        "grid": grid,
        "bytes": os.path.getsize(out),
        "ms": int((time.time() - t0) * 1000),
    }
