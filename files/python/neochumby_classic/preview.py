"""preview: put a page or a screen together the way the Chumby's engine draws it, as a PNG.

For checking a design, and for the README's screenshots (tests/smoke.py --screenshots).
A still: sprites show their first frame, scrolling layers their start, panes the frame
for the given state.

Usage: python -m neochumby_classic.preview OUTDIR page N|screen NAME OUT.png [var=value ...] [time=7:42PM]
"""
import os
import sys

from PIL import Image, ImageChops

from .output import unpack565
from .widget import H, W

KEY = (255, 0, 255)            # 0xF81F, the transparent colour, after a round trip through RGB565
GLYPHS = "0123456789: APM"


def load(out, name, w, h):
    with open(os.path.join(out, name), "rb") as fh:
        data = fh.read(w * h * 2)
    return unpack565(data, (w, h))


def keyed(img):
    """A mask that hides the transparent colour."""
    r, g, b = ImageChops.difference(img.convert("RGB"), Image.new("RGB", img.size, KEY)).split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)


def manifest(out):
    with open(os.path.join(out, "manifest.txt")) as fh:
        return [ln.split() for ln in fh.read().splitlines() if ln and not ln.startswith("#")]


def page(out, n, night=False, clock="7:42 PM", ticker_at=260):
    lines = manifest(out)
    n = str(n)
    mine = [t for t in lines if len(t) > 1 and t[1] == n]
    bg = next((t[2] for t in mine if t[0] == "pagenight"), None) if night else None
    pg = next(t for t in lines if t[0] == "page" and t[1] == n)
    img = load(out, bg or pg[2], W, H)
    for t in mine:
        if t[0] == "bgscroll":
            img.paste(load(out, t[2], int(t[3]), H).crop((0, 0, W, H)), (0, 0))
    for t in mine:
        if t[0] == "glow":
            w, h, y = int(t[3]), int(t[4]), int(t[5])
            band = img.crop((0, y, W, y + h))
            img.paste(ImageChops.add(band, load(out, t[2], w, h).crop((0, 0, W, h))), (0, y))
        elif t[0] == "sprite":
            w, h, x, y = int(t[3]), int(t[4]), int(t[6]), int(t[7])
            fr = load(out, t[2], w, h)
            img.paste(fr, (x, y), keyed(fr))
        elif t[0] == "logofx":
            w, h, x, y = map(int, t[3:7])
            lg = load(out, t[2], w, h)
            img.paste(lg, (x, y), keyed(lg))
    if "noclock" not in pg:
        c = next((t for t in lines if t[0] == "clock"), None)
        if c:
            cw, ch, right, y = map(int, c[2:6])
            widths = list(map(int, c[6:21]))
            cells = load(out, c[1], cw, ch * len(GLYPHS))
            x = right - sum(widths[GLYPHS.index(g)] for g in clock)
            for g in clock:
                k = GLYPHS.index(g)
                img.paste(cells.crop((0, k * ch, widths[k], k * ch + ch)), (x, y))
                x += widths[k]
    if "noticker" not in pg:
        t = next((t for t in lines if t[0] == "ticker"), None)
        if t:
            w, h, y = int(t[2]), int(t[3]), int(t[4])
            img.paste(load(out, t[1], w, h).crop((ticker_at, 0, ticker_at + W, h)), (0, y))
    return img


def screen(out, name, state=None):
    state = dict({"music": 0, "station": 0, "night": 0, "pin": 0, "page": 0, "vol": 65}, **(state or {}))
    lines = manifest(out)
    s = next(t for t in lines if t[0] == "screen" and t[1] == name)
    img = load(out, s[2], W, H)
    for p in lines:
        if len(p) < 2 or p[1] != name:
            continue
        if p[0] == "pane":
            w, h, x, y = map(int, p[3:7])
            frames = int(p[8])
            fr = min(state.get(p[7], 0), frames - 1)
            img.paste(load(out, p[2], w, h * frames).crop((0, fr * h, w, fr * h + h)), (x, y))
        elif p[0] == "slider":
            w, h, x, y, t0, t1, kw, kh = map(int, p[5:13])
            empty, full, knob = load(out, p[2], w, h), load(out, p[3], w, h), load(out, p[4], kw, kh)
            kx = t0 + (t1 - t0) * state["vol"] // 100
            empty.paste(full.crop((t0, 0, kx, h)), (t0, 0))
            empty.paste(knob, (kx - kw // 2, (h - kh) // 2), keyed(knob))
            img.paste(empty, (x, y))
    return img


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if len(a) < 4 or a[1] not in ("page", "screen"):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 64
    out, kind, which, dst = a[:4]
    kv = dict(x.split("=", 1) for x in a[4:] if "=" in x)
    if kind == "page":
        img = page(out, int(which), night=kv.get("night") == "1", clock=kv.get("time", "7:42 PM").replace("PM", " PM"))
    else:
        img = screen(out, which, {k: int(v) for k, v in kv.items() if v.isdigit()})
    img.save(dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
