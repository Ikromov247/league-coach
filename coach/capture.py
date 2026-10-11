"""Screen capture: a downscaled full frame plus an enlarged minimap crop."""

import base64
import io

import mss
from PIL import Image

from .config import FRAME_WIDTH, MINIMAP_FRACTION


def _jpeg_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode()


def grab() -> tuple[str, str]:
    """Returns (frame, minimap) as base64 JPEGs of the primary display."""
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    w, h = img.size
    side = int(h * MINIMAP_FRACTION)
    minimap = img.crop((w - side, h - side, w, h)).resize(
        (512, 512), Image.Resampling.LANCZOS)
    frame = img.resize(
        (FRAME_WIDTH, round(FRAME_WIDTH * h / w)), Image.Resampling.LANCZOS)
    return _jpeg_b64(frame), _jpeg_b64(minimap)
