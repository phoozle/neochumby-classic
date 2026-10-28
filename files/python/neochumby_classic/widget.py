"""widget: the widget API, version 1 (docs/widgets.md is the full description).

A widget is a Python module, found through the entry point group
"neochumby_classic.widgets" (the entry point's name is the widget's id). The built-in
widgets are found the same way, from this package's own pyproject.toml. The module
provides:

    API = 1
    APP = {"id", "name", "icon", "description", "seconds", "subslides": [...]}
    CONFIG_SCHEMA = [fields]                        (optional)
    def page_count(subslide_id, options) -> int     (optional, default 1)
    def render(ctx: Context) -> Rendered

The renderer calls render() once per render for each widget enabled in the Chumby's
channel, with the slides of it that fit in the slideshow (none, if it was cut).
"""
import dataclasses
import importlib.metadata
import os
import sys

from PIL import ImageFont

from . import assets, output

API_VERSION = 1
GROUP = "neochumby_classic.widgets"
W, H = 320, 240                                  # the Chumby's screen
# Values the engine reads on the Chumby itself and draws without a render (docs/widgets.md,
# "Live values"): `live` boxes show their text, and a `pane` can take wifi or wifi_pct as its
# variable. LIVE_CHARSET is a `livefont` strip's 79 glyphs, in order.
LIVE_KEYS = ("wifi", "wifi_pct", "ip", "ssid", "uptime", "mem_free", "stick_free", "firmware", "name")
LIVE_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz .:%/-?_()+,'#&@!"


@dataclasses.dataclass
class Rendered:
    """What a widget's render() returns.

    lines       manifest directives (docs/widgets.md lists the ones a widget may use)
    titles      one title per slideshow page it added, in order (Home shows them)
    thumbs      one 320x240 PIL image per slideshow page it added (Home's thumbnail)
    home_title  the name on Home's card, if not APP["name"] (Weather: "<town> Weather")
    facts       short facts for Settings -> Chumby Info, e.g. {"feed": "weather of ..."}
    latlon      the place it shows, (latitude, longitude), for the sunrise and sunset that
                drive night mode when the owner hasn't set them (Weather: its station's)
    """
    lines: list = dataclasses.field(default_factory=list)
    titles: list = dataclasses.field(default_factory=list)
    thumbs: list = dataclasses.field(default_factory=list)
    home_title: str = None
    facts: dict = dataclasses.field(default_factory=dict)
    latlon: tuple = None


@dataclasses.dataclass
class Context:
    """Everything render() gets.

    out          the Chumby's output directory: every file goes straight in it
    first_page   the slideshow number of this widget's first page
    slides       [(subslide_id or None, pages)]: what to draw, in order; [] if none fit
    seconds      seconds per slide, from the channel
    options      the widget's options, validated against CONFIG_SCHEMA
    now          the render's time (aware, in the configured time zone)
    tz           that time zone (a ZoneInfo)
    chumby       the Chumby's name, or None
    state_dir    a directory of the widget's own for caches: kept between renders
    config_url   the web page's address (for "set this up on the web page" hints)
    user_agent   the User-Agent to send with every fetch
    """
    out: str
    first_page: int
    slides: list
    seconds: int
    options: dict
    now: object
    tz: object
    chumby: str = None
    state_dir: str = None
    config_url: str = ""
    user_agent: str = ""

    def path(self, name):
        """The full path of output file `name` (one word: letters, digits, '_', '.', '-')."""
        if not name or os.sep in name or name.startswith("."):
            raise ValueError(f"bad output file name {name!r}")
        return os.path.join(self.out, name)

    def write565(self, img, name):
        """Write a picture as RGB565 under `name`, if its bytes changed. True if it wrote."""
        return output.rgb565(img, self.path(name))

    def write(self, name, data):
        """Write raw bytes (an .l8 frame, a path file) under `name`, if they changed."""
        return output.write_if_changed(self.path(name), data)

    def font(self, file, size):
        """A bundled font (data/fonts/README.md lists them) at `size` px."""
        return ImageFont.truetype(os.path.join(assets.FONTS, file), size)

    def asset(self, name):
        """The path of an owner-supplied picture (assets.py), or None."""
        return assets.find(name)

    @property
    def pages(self):
        return sum(n for _, n in self.slides)


# ---------------------------------------------------------------- discovery
DIST = "neochumby-classic"
# The built-in widgets' entry points, as pyproject.toml declares them. Used only when this
# package runs without being installed (from a source tree on PYTHONPATH), where Python
# has no metadata to find entry points in; tests/smoke.py checks the two lists agree.
BUILTIN = {
    "aurora": "neochumby_classic.widgets.aurora",
    "news": "neochumby_classic.widgets.news",
    "photos": "neochumby_classic.widgets.photos",
    "stocks": "neochumby_classic.widgets.stocks",
    "weather": "neochumby_classic.widgets.weather",
    "whyallanews": "neochumby_classic.widgets.whyallanews",
}


def entry_points():
    """The widget entry points: the installed ones, or BUILTIN when this package isn't installed."""
    eps = list(importlib.metadata.entry_points(group=GROUP))
    try:
        importlib.metadata.distribution(DIST)
    except importlib.metadata.PackageNotFoundError:
        eps += [importlib.metadata.EntryPoint(name, value, GROUP) for name, value in BUILTIN.items()]
    return sorted(eps, key=lambda e: e.name)


def discover():
    """{widget id: module} for every installed widget that speaks API 1."""
    found = {}
    for ep in entry_points():
        try:
            mod = ep.load()
        except Exception as e:                    # a broken widget must not take the rest down
            print(f"widgets: can't load {ep.name} ({ep.value}): {e}", file=sys.stderr)
            continue
        problem = check(ep.name, mod)
        if problem:
            print(f"widgets: skipping {ep.name}: {problem}", file=sys.stderr)
            continue
        if ep.name in found:
            print(f"widgets: two widgets are called {ep.name}; keeping the first", file=sys.stderr)
            continue
        found[ep.name] = mod
    return found


def check(name, mod):
    """Why `mod` isn't a usable API 1 widget called `name`, or None."""
    if getattr(mod, "API", None) != API_VERSION:
        return f"API is {getattr(mod, 'API', None)!r}, not {API_VERSION}"
    app = getattr(mod, "APP", None)
    if not isinstance(app, dict) or app.get("id") != name:
        return "APP['id'] doesn't match the entry point's name"
    if not callable(getattr(mod, "render", None)):
        return "no render(ctx)"
    return None


def icon_path(mod):
    """The widget's icon: APP["icon"], relative to the module's own directory."""
    icon = (getattr(mod, "APP", None) or {}).get("icon") or ""
    if not icon or icon.startswith("/") or ".." in icon.split("/"):
        return None
    p = os.path.join(os.path.dirname(os.path.abspath(mod.__file__)), icon)
    return p if os.path.isfile(p) else None
