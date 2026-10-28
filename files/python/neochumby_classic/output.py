"""output: how every file in a Chumby's output directory is written.

The rendering contract (docs/widgets.md) in code: a file is written only when its bytes
change, and then under a dot name beside it that is renamed into place, so a reader never
sees half a file and an unchanged file keeps its mtime.
"""
import os

from PIL import Image, ImageChops


def write_if_changed(path, data):
    """Write `data` to `path` unless the file already holds exactly that. True if it wrote."""
    try:
        if os.path.getsize(path) == len(data):
            with open(path, "rb") as fh:
                if fh.read() == data:
                    return False
    except OSError:
        pass
    tmp = os.path.join(os.path.dirname(path) or ".", f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def pack565(img):
    """RGB -> RGB565 little-endian bytes (the Chumby's framebuffer format), with Pillow's
    channel operations rather than a Python loop over every pixel."""
    r, g, b = img.convert("RGB").split()
    hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    lo = ImageChops.add(g.point(lambda v: ((v >> 2) & 7) << 5), b.point(lambda v: v >> 3))
    return Image.merge("LA", (lo, hi)).tobytes()


def unpack565(data, size):
    """RGB565 bytes -> RGB, as the screen shows them."""
    lo = Image.frombytes("L", size, data[0::2])
    hi = Image.frombytes("L", size, data[1::2])
    r = hi.point(lambda v: (v & 0xF8) | (v >> 5))
    g = ImageChops.add(hi.point(lambda v: (v & 7) << 5), lo.point(lambda v: (v >> 5) << 2))
    g = g.point(lambda v: v | (v >> 6))
    b = lo.point(lambda v: ((v & 31) << 3) | ((v & 31) >> 2))
    return Image.merge("RGB", (r, g, b))


def rgb565(img, path):
    """Write a picture as RGB565, if its bytes changed. True if it wrote."""
    return write_if_changed(path, pack565(img))
