"""preview: put a page or a screen together the way the Chumby's engine draws it, as a PNG.

For checking a design, and for the README's screenshots (tests/smoke.py --screenshots).
A still: sprites show their first frame, scrolling layers their start, panes the frame
for the given state, and live boxes the given device values (EXAMPLE, made up, for the
screenshots; a value not given is "?", or 0 bars).

Usage: python -m neochumby_classic.preview OUTDIR page N|screen NAME OUT.png [var=value ...] [time=7:42PM]
       (a var that is a live key, ip=192.0.2.7, sets that value)
"""
import os
import sys

from PIL import Image, ImageChops

from .output import unpack565
from .widget import H, LIVE_CHARSET, LIVE_KEYS, W

KEY = (255, 0, 255)            # 0xF81F, the transparent colour, after a round trip through RGB565
GLYPHS = "0123456789: APM"
EXAMPLE = {"wifi": "3", "wifi_pct": "68%", "ip": "192.0.2.7", "ssid": "Home WiFi", "uptime": "3d 4h",
           "mem_free": "33.1 MB", "stick_free": "1.4 GB", "firmware": "1.7.3", "name": "kitchen"}


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


def live_values(out, lines, img, target, values, base=None):
    """Draw the panes and live boxes for `target` (a screen, page:N or pagenight:N) as the
    engine would with these device values. base: the background picture, for BG "bg"."""
    values = values or {}
    fonts = {}
    base = base or img.copy()
    for t in lines:
        if t[0] == "pane" and t[1] == target and t[7] in ("wifi", "wifi_pct"):
            w, h, x, y, frames = int(t[3]), int(t[4]), int(t[5]), int(t[6]), int(t[8])
            v = values.get(t[7], "0").rstrip("%")
            fr = max(0, min(int(v) if v.isdigit() else 0, frames - 1))
            img.paste(load(out, t[2], w, h * frames).crop((0, fr * h, w, fr * h + h)), (x, y))
        elif t[0] == "livefont":
            cw, ch = int(t[3]), int(t[4])
            widths = [min(cw, int(v)) for v in t[5:]] or [cw] * len(LIVE_CHARSET)
            fonts[t[1]] = (load(out, t[2], cw, ch * len(LIVE_CHARSET)), cw, ch, widths)
        elif t[0] == "live" and t[1] == target and t[3] in fonts:
            strip, cw, ch, widths = fonts[t[3]]
            x, y, w, h = map(int, t[4:8])
            text = values.get(t[2], "?")[:32]
            idx = [LIVE_CHARSET.index(c) if c in LIVE_CHARSET else LIVE_CHARSET.index("?") for c in text]
            box = base.crop((x, y, x + w, y + h)) if t[9] == "bg" else Image.new("RGB", (w, h), tuple(
                int(t[9][i:i + 2], 16) for i in (1, 3, 5)))
            tw = sum(widths[i] for i in idx)
            px = {"left": 0, "right": w - tw, "center": int((w - tw) / 2)}[t[8]]
            for i in idx:
                g = strip.crop((0, i * ch, widths[i], i * ch + ch))
                box.paste(g, (px, 0), keyed(g))
                px += widths[i]
            img.paste(box, (x, y))
    return img


def page(out, n, night=False, clock="7:42 PM", ticker_at=260, values=None):
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
    targets = {t[1] for t in lines if t[0] in ("pane", "live")}
    target = f"pagenight:{n}" if night and f"pagenight:{n}" in targets else f"page:{n}"
    live_values(out, lines, img, target, values)
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


def screen(out, name, state=None, values=None):
    state = dict({"player": 0, "night": 0, "pin": 0, "page": 0, "vol": 65}, **(state or {}))
    lines = manifest(out)
    s = next(t for t in lines if t[0] == "screen" and t[1] == name)
    img = load(out, s[2], W, H)
    base = img.copy()
    for p in lines:
        if len(p) < 2 or p[1] != name:
            continue
        if p[0] == "pane" and p[7] in ("wifi", "wifi_pct"):
            continue                                   # live_values() draws these
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
    return live_values(out, lines, img, name, values, base)


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if len(a) < 4 or a[1] not in ("page", "screen"):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 64
    out, kind, which, dst = a[:4]
    kv = dict(x.split("=", 1) for x in a[4:] if "=" in x)
    values = {k: v for k, v in kv.items() if k in LIVE_KEYS}
    if kind == "page":
        img = page(out, int(which), night=kv.get("night") == "1", clock=kv.get("time", "7:42 PM").replace("PM", " PM"),
                   values=values)
    else:
        img = screen(out, which, {k: int(v) for k, v in kv.items() if v.isdigit() and k not in LIVE_KEYS}, values)
    img.save(dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
