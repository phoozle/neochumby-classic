"""flurry: the Mac OS X Flurry screensaver, drawn live on the Chumby.

Flurry is Calum Robinson's (BSD licence). The engine runs it on the Chumby itself, in
fixed point (neochumby's apps/classic/src/flurry.rs, the `flurry` directive), so this
widget draws nothing of it: it writes one page and the line that turns it into Flurry,
with the options below.

The page's own picture is a still of glowing streams, drawn here with Pillow (nothing of
Apple's). The engine draws Flurry over it; an engine without the directive shows the
still instead of a black page.
"""
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..widget import Rendered

W, H = 320, 240
API = 1
APP = {"id": "flurry", "name": "Flurry Screensaver", "icon": "icons/flurry.png", "seconds": 30,
       "description": "The Mac OS X screensaver: glowing streams of colour, live on the Chumby.", "subslides": []}

# The engine's presets and colour modes (flurry::PRESETS and flurry::COLOURS).
PRESETS = [["classic", "Classic (as on the Mac)"], ["rgb", "RGB"], ["water", "Water"], ["fire", "Fire (Big Flurry)"],
           ["psychedelic", "Psychedelic"], ["random", "A different one each time"]]
COLOURS = ["red", "magenta", "blue", "cyan", "green", "yellow", "slowcyclic", "cyclic", "tiedye", "rainbow",
           "white", "multi", "dark"]
COLOUR_NAMES = {"slowcyclic": "Slow cycle", "cyclic": "Cycle", "tiedye": "Tie-dye", "multi": "Pastel"}
SPEEDS = ["0.25", "0.5", "1", "1.5", "2"]
MAX_STREAMS = 12          # the engine takes 16; more than 12 drops its frame rate on a Chumby
CONFIG_SCHEMA = [
    {"key": "preset", "type": "choice", "label": "Preset", "default": "classic", "choices": PRESETS},
    {"key": "streams", "type": "choice", "label": "Streams", "default": "preset",
     "choices": [["preset", "As the preset"]] + [[str(n), str(n)] for n in range(1, MAX_STREAMS + 1)],
     "help": "More streams cost the Chumby more: past about 6 it releases fewer puffs from each."},
    {"key": "color", "type": "choice", "label": "Colours", "default": "preset",
     "choices": [["preset", "As the preset"]] + [[c, COLOUR_NAMES.get(c, c.capitalize())] for c in COLOURS]},
    {"key": "speed", "type": "choice", "label": "Speed", "default": "preset",
     "choices": [["preset", "As the preset"]] + [[s, f"{s}x"] for s in SPEEDS]},
]


def line(page, opts=None):
    """The engine's directive for these options (validated by config.validate; checked again here)."""
    opts = opts or {}
    preset = opts.get("preset", "classic")
    words = [preset if preset in [p for p, _ in PRESETS] else "classic"]
    streams, colour, speed = opts.get("streams"), opts.get("color"), opts.get("speed")
    if str(streams).isdigit() and 1 <= int(streams) <= MAX_STREAMS:
        words.append(f"streams={int(streams)}")
    if colour in COLOURS:
        words.append(f"color={colour}")
    if speed in SPEEDS:
        words.append(f"speed={speed}")
    return f"flurry {page} " + " ".join(words)


def still(w=W, h=H):
    """Streams of light from a point, as Flurry looks at a moment: our own drawing."""
    k = 3                                                   # supersampling
    img = Image.new("RGB", (w * k, h * k))
    cx, cy = w * k * 0.56, h * k * 0.6
    streams = [(-2.6, 0.9, (90, 140, 255)), (-2.1, -0.7, (80, 230, 160)), (-1.2, 1.3, (255, 120, 200)),
               (0.4, -1.1, (255, 190, 90)), (2.4, 0.6, (140, 110, 255))]
    for a0, bend, col in streams:
        layer = Image.new("RGB", img.size)
        d = ImageDraw.Draw(layer)
        pts = []
        for i in range(60):
            t = i / 59
            a = a0 + bend * t * t
            r = t * w * k * 0.62
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r * 0.8))
        for i in range(len(pts) - 1):
            t = i / (len(pts) - 1)
            fade = (1 - t) ** 1.4
            c = tuple(int(v * fade) for v in col)
            d.line([pts[i], pts[i + 1]], fill=c, width=max(1, int(k * (2 + 7 * t))))
        img = ImageChops.add(img, layer.filter(ImageFilter.GaussianBlur(k * 2.5)))
        img = ImageChops.add(img, layer.filter(ImageFilter.GaussianBlur(k * 0.8)))
    core = Image.new("RGB", img.size)
    ImageDraw.Draw(core).ellipse([cx - 9 * k, cy - 9 * k, cx + 9 * k, cy + 9 * k], fill=(255, 250, 235))
    img = ImageChops.add(img, core.filter(ImageFilter.GaussianBlur(k * 4)))
    return img.resize((w, h), Image.LANCZOS)


# ---------------------------------------------------------------- widget API 1
def render(ctx):
    """API 1 (docs/widgets.md): one page, which the engine draws Flurry on."""
    if not ctx.slides:
        return Rendered()
    from ..output import rgb565
    pic = still()
    rgb565(pic, f"{ctx.out}/flurry_still.raw")
    page = ctx.first_page
    lines = [f"page {page} flurry_still.raw {ctx.seconds} noclock noticker", line(page, ctx.options)]
    return Rendered(lines, ["Flurry Screensaver"], [pic])
