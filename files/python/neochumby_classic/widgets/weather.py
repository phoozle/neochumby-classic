"""weather: the Weather widget: Australian weather from the Bureau of Meteorology, for any
place in Australia, in a 90s weather channel's look.

Pages (each with a dark copy for night mode):
  weather_current, weather_local, weather_extended   320x240 page backgrounds, clock area left blank
  weather_<page>_icon<N>                             animated icon sprite sheets (frames stacked
                                                     vertically, 0xF81F = transparent)
  weather_setup                                      until a place is chosen: where to choose it
and, for the whole slideshow, the clock and the ticker the engine draws over these pages:
  clock.raw, clock_night.raw     glyph cells for "0123456789: APM", on the header background
  ticker.raw, ticker_night.raw   a long 24 px strip the engine scrolls: the weather now and the
                                 next three days' forecasts (weather only: news has its own apps)

BOM's website blocks scrapers and points to its anonymous FTP, so observations and forecasts
come from there: each state's observations product (every station's latest reading, with
its wmo-id, name, latitude and longitude) and its précis town forecasts (each town's aac).
The owner picks a state, then a station from that state's list, on the web page (the
choices() hook); the forecast town is the one with the station's name unless they pick
another. The station's latitude and longitude also drive night mode (Rendered.latlon).
There is no default place: until one is chosen, the widget shows where to choose it.
"""
import datetime as dt
import math
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from .. import assets, config
from ..widget import Rendered

API = 1
APP = {"id": "weather", "name": "Australian Weather", "icon": "icons/weather.png", "seconds": 12,
       "description": "Weather from the Bureau of Meteorology for any place in Australia, with a scrolling weather ticker.",
       "subslides": [{"id": "current", "label": "Current Conditions", "default": True},
                     {"id": "local", "label": "Local Forecast", "default": True},
                     {"id": "extended", "label": "Extended Forecast", "default": True}]}

# BOM's anonymous FTP products (ftp://ftp.bom.gov.au/anon/gen/fwo/), per state and territory:
# (name, observations, précis town forecasts, the observations' district for this state).
# The ACT's stations are in New South Wales' observations (district NSW_PW017), and its
# town forecast (Canberra) is a product of its own.
STATES = {
    "ACT": ("Australian Capital Territory", "IDN60920", "IDN10035", "NSW_PW017"),
    "NSW": ("New South Wales", "IDN60920", "IDN11060", None),
    "NT": ("Northern Territory", "IDD60920", "IDD10207", None),
    "QLD": ("Queensland", "IDQ60920", "IDQ11295", None),
    "SA": ("South Australia", "IDS60920", "IDS10044", None),
    "TAS": ("Tasmania", "IDT60920", "IDT16710", None),
    "VIC": ("Victoria", "IDV60920", "IDV10753", None),
    "WA": ("Western Australia", "IDW60920", "IDW14199", None),
}
CONFIG_SCHEMA = [
    {"key": "state", "type": "choice", "label": "State or territory", "default": "",
     "choices": [["", "Choose one"]] + [[k, v[0]] for k, v in STATES.items()],
     "help": "Then pick the weather station nearest you: the list below follows the state."},
    {"key": "station", "type": "string", "label": "Weather station", "default": "", "optional": True, "suggest": True,
     "max_len": 5, "pattern": r"[0-9]{5}",
     "help": "Pick one from the list (the state's observation stations), or type a station's wmo-id (Whyalla: 95664)."},
    {"key": "forecast_area", "type": "string", "label": "Forecast town", "default": "", "optional": True, "suggest": True,
     "max_len": 10, "pattern": r"[A-Z]{2,3}_[A-Z]{2}[0-9]{3}",
     "help": "Blank: the town with the station's name. Or pick one from the list, or type its aac (Whyalla: SA_PT055)."},
    {"key": "name", "type": "string", "label": "Name on the pages", "default": "", "optional": True, "max_len": 20,
     "pattern": r"[A-Za-z][A-Za-z .'\-]{0,19}", "help": "Blank: the station's name."},
]
OLD_KEYS = ("location", "obs_product", "obs_station", "forecast_product", "forecast_area")


def migrate(opts):
    """Options from before the state picker (BOM product ids and a place name) -> today's."""
    if "obs_station" not in opts or "station" in opts:
        return opts
    state = next((k for k, v in STATES.items() if (v[1], v[2]) == (opts.get("obs_product"), opts.get("forecast_product"))),
                 None) or next((k for k, v in STATES.items() if v[1] == opts.get("obs_product")), "")
    return {"state": state, "station": opts.get("obs_station", ""), "forecast_area": opts.get("forecast_area", ""),
            "name": opts.get("location", "")}


W, H = 320, 240
UA = config.USER_AGENT
BOM_FTP = "ftp://ftp.bom.gov.au/anon/gen/fwo/{}.xml"
LIST_TTL = 86400               # the web page's station and town lists: a day old is fine
LOC = {"name": ""}             # the place being drawn (render() sets it)
KEY = (255, 0, 255)                                                     # sprite colour key (0xF81F)

# Bundled open fonts, metric-compatible with the Arial the pages were designed in, so every
# server renders the same pixels (see data/fonts/README.md)
FONT_DIR = assets.FONTS
BOLD = FONT_DIR + "LiberationSans-Bold.ttf"              # Arial Bold
NARROW = FONT_DIR + "LiberationSansNarrow-Bold.ttf"      # Arial Narrow Bold

# Palette: WeatherStar-4000-inspired, not copied. Two themes: the day one, and a darker
# navy one with dimmer accents for night mode (the pages' pagenight files, the clock and
# ticker's clocknight / tickernight), which keeps the 90s look without glaring in the dark.
THEMES = {
    "day": dict(SKY_TOP=(28, 10, 87), SKY_BOT=(64, 44, 152),      # page background gradient
                HDR_TOP=(16, 18, 64), HDR_BOT=(40, 58, 140),      # header band
                PANEL=(34, 52, 150), ORANGE=(230, 120, 20), YELLOW=(255, 230, 60), WHITE=(240, 240, 240),
                TICK_TOP=(10, 30, 90), TICK_BOT=(20, 50, 130),    # ticker band
                FAINT=(170, 180, 230), RAIN=(140, 200, 255), LOGO_DIM=1.0),
    "night": dict(SKY_TOP=(5, 4, 24), SKY_BOT=(12, 12, 48),
                  HDR_TOP=(3, 4, 18), HDR_BOT=(10, 16, 50),
                  PANEL=(12, 20, 64), ORANGE=(140, 72, 14), YELLOW=(196, 170, 64), WHITE=(178, 180, 194),
                  TICK_TOP=(3, 8, 30), TICK_BOT=(7, 16, 48),
                  FAINT=(100, 108, 150), RAIN=(84, 128, 176), LOGO_DIM=0.7),
}
SHADOW = (0, 0, 0)
THEME = "day"


def set_theme(name):
    """Rebind the palette globals the drawing code reads (day or night)."""
    global THEME
    THEME = name
    globals().update(THEMES[name])


# the palette names set_theme() binds, declared so the code reading them is checkable
SKY_TOP = SKY_BOT = HDR_TOP = HDR_BOT = PANEL = ORANGE = YELLOW = WHITE = TICK_TOP = TICK_BOT = None
FAINT = RAIN = LOGO_DIM = None
set_theme("day")

HEADER_H = 40
TICKER_Y, TICKER_H = 216, 24
_logo = {}


def fit_font(text, path, width, size, smallest):
    """The largest font from `size` down to `smallest` that fits `text` in `width` px."""
    for n in range(size, smallest - 1, -1):
        f = ImageFont.truetype(path, n)
        if f.getlength(text) <= width:
            return f
    return ImageFont.truetype(path, smallest)


def ellipsize_to(text, f, width):
    while f.getlength(text) > width and len(text) > 2:
        text = text[:-2].rstrip() + "…"
    return text


def badge(name=""):
    """The weather pages' badge when no weather-badge.png is supplied, drawn at 4x and shrunk:
    on a navy tile with an orange rim, the place's name across the top, and a sun behind a
    cloud beside WEATHER under it (without a name: the sun and cloud over WEATHER). The same
    size as the original badge, so the pages' layout doesn't move."""
    k, w, h = 4, 120, 96
    im = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([2 * k, 2 * k, (w - 2) * k, (h - 2) * k], radius=16 * k, fill=(30, 40, 120), outline=(230, 120, 20),
                        width=5 * k)

    def text(xy, s, f, fill, anchor):
        d.text((xy[0] + 2 * k, xy[1] + 2 * k), s, font=f, fill=(0, 0, 0, 200), anchor=anchor)
        d.text(xy, s, font=f, fill=fill, anchor=anchor)
    town = name.upper().strip()
    if town:
        f = fit_font(town, NARROW, 102 * k, 40 * k, 24 * k)
        words = town.split()
        if f.getlength(town) > 102 * k and len(words) > 1:      # two lines: "ALICE / SPRINGS"
            cut = min(range(1, len(words)), key=lambda n: abs(len(" ".join(words[:n])) - len(" ".join(words[n:]))))
            rows = [" ".join(words[:cut]), " ".join(words[cut:])]
            f = min((fit_font(r, NARROW, 102 * k, 24 * k, 14 * k) for r in rows), key=lambda x: x.size)
            rows = [ellipsize_to(r, f, 102 * k) for r in rows]
            text((60 * k, 26 * k), rows[0], f, (255, 255, 255, 255), "ms")
            text((60 * k, 48 * k), rows[1], f, (255, 255, 255, 255), "ms")
        else:
            f = fit_font(town, NARROW, 102 * k, 40 * k, 16 * k)
            text((60 * k, 44 * k), ellipsize_to(town, f, 102 * k), f, (255, 255, 255, 255), "ms")
        draw_sun(d, 22 * k, 62 * k, 22 * k, 0.3)
        draw_cloud(d, 30 * k, 72 * k, 24 * k)
        text((46 * k, 82 * k), "WEATHER", fit_font("WEATHER", BOLD, 64 * k, 30 * k, 10 * k), (255, 230, 60, 255), "ls")
    else:
        draw_sun(d, 52 * k, 30 * k, 34 * k, 0.3)
        draw_cloud(d, 66 * k, 44 * k, 36 * k)
        text((60 * k, 84 * k), "WEATHER", fit_font("WEATHER", BOLD, 102 * k, 30 * k, 10 * k), (255, 230, 60, 255), "ms")
    return im.resize((w, h), Image.LANCZOS)


def header_logo(h=34):
    """The badge (an owner's weather-badge.png, else ours with the place's name), dimmed in
    the night theme (its white and yellow would glare)."""
    key = (THEME, LOC["name"], h)
    if key not in _logo:
        path = assets.find("weather-badge.png")
        src = Image.open(path).convert("RGBA") if path else badge(LOC["name"])
        im = src.resize((round(src.width * h / src.height), h), Image.LANCZOS)
        if LOGO_DIM != 1.0:
            r, g, b, a = im.split()
            r, g, b = (c.point(lambda v: int(v * LOGO_DIM)) for c in (r, g, b))
            im = Image.merge("RGBA", (r, g, b, a))
        _logo[key] = im
    return _logo[key]
CLOCK_R, CLOCK_Y = W - 6, 22   # the device right-aligns the time to CLOCK_R


def font(size, path=BOLD):
    return ImageFont.truetype(path, size)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(8_000_000)


def cache_dir():
    """The widget's cache: the renderer's ctx.state_dir, which the web page reads too."""
    return os.path.join(config.state_dir(), "widgets", "weather")


def product(pid, cache, ttl=0, fetch_ok=True):
    """A BOM product's XML. ttl=0: fetch it now (the renderer), falling back to the cached
    copy if BOM can't be reached; ttl>0: the cached copy if it's younger (the web page).
    fetch_ok=False: only ever the cached copy, or None. Every fetch refreshes the cache."""
    path = os.path.join(cache, pid + ".xml") if cache else None
    cached = path and os.path.isfile(path)
    if cached and (not fetch_ok or (ttl and time.time() - os.path.getmtime(path) < ttl)):
        with open(path, "rb") as fh:
            return fh.read()
    if not fetch_ok:
        return None
    try:
        data = fetch(BOM_FTP.format(pid))
        ET.fromstring(data)                                       # only keep what parses
    except Exception as e:
        if not cached:
            raise
        print(f"weather: {pid} unavailable ({e}); using the last copy", file=sys.stderr)
        with open(path, "rb") as fh:
            return fh.read()
    if path:
        os.makedirs(cache, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    return data


# ---------------------------------------------------------------- data
def stations(xml, district=None):
    """[{wmo, name, lat, lon, district}] in an observations product, by name."""
    out = []
    for s in ET.fromstring(xml).iter("station"):
        wmo, name = s.get("wmo-id") or "", (s.get("description") or s.get("stn-name") or "").strip()
        if not re.fullmatch(r"[0-9]{5}", wmo) or not name or (district and s.get("forecast-district-id") != district):
            continue
        try:
            lat, lon = float(s.get("lat")), float(s.get("lon"))
        except (TypeError, ValueError):
            lat = lon = None
        out.append({"wmo": wmo, "name": name, "lat": lat, "lon": lon, "district": s.get("forecast-district-id") or ""})
    return sorted(out, key=lambda x: x["name"].lower())


def towns(xml):
    """[{aac, name, district}]: the forecast towns in a précis product, by name."""
    out = [{"aac": a.get("aac"), "name": (a.get("description") or "").strip(), "district": a.get("parent-aac") or ""}
           for a in ET.fromstring(xml).iter("area") if a.get("type") == "location" and a.get("aac")]
    return sorted(out, key=lambda x: x["name"].lower())


def short_name(name):
    """A station's name as a place: "Sydney - Observatory Hill" -> "Sydney", "Darwin Airport" -> "Darwin"."""
    s = re.split(r" - | \(|,", name)[0]
    s = re.sub(r"\s+(Airport|Aero|AWS|Ap|Airfield|RAAF|Amo)\b.*$", "", s, flags=re.I).strip()
    return s or name


def _words(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split()


def match_town(station, town_list):
    """The forecast town for a station: the one with its name, or its name without the
    "Airport" or " - Observatory Hill" part; else one in the station's district whose name
    starts with the same word. None if nothing fits (the owner then picks one)."""
    by_name = {" ".join(_words(t["name"])): t for t in town_list}
    for cand in (station["name"], short_name(station["name"])):
        t = by_name.get(" ".join(_words(cand)))
        if t:
            return t
    first = _words(short_name(station["name"]))[:1]
    return next((t for t in town_list if first and _words(t["name"])[:1] == first
                 and t["district"] == station["district"]), None)


def get_obs(xml, wmo):
    """(the station's latest observation, mapped to the keys the pages use, and the station),
    or (None, None) if the product has no such station."""
    st = next((s for s in ET.fromstring(xml).iter("station") if s.get("wmo-id") == wmo), None)
    if st is None:
        return None, None
    info = next((s for s in stations(xml) if s["wmo"] == wmo), {"wmo": wmo, "name": wmo, "lat": None, "lon": None,
                                                                "district": ""})
    per = next(st.iter("period"), None)
    e = {x.get("type"): x.text for x in per.iter("element")} if per is not None else {}
    num = lambda k: float(e[k]) if e.get(k) not in (None, "") else None
    return ({"air_temp": num("air_temperature"), "apparent_t": e.get("apparent_temp", "--"),
             "rel_hum": e.get("rel-humidity", "--"), "wind_dir": e.get("wind_dir", ""),
             "wind_spd_kmh": e.get("wind_spd_kmh", "--"), "gust_kmh": e.get("gust_kmh", "--"),
             "press": e.get("msl_pres", "--"), "rain_trace": e.get("rainfall", "--"),
             "weather": e.get("weather"), "cloud": e.get("cloud"),
             "max": e.get("maximum_air_temperature"), "min": e.get("minimum_air_temperature"),
             "time": per.get("time-local", "") if per is not None else ""}, info)


def get_forecast(xml, aac):
    for a in ET.fromstring(xml).iter("area"):
        if a.get("aac") == aac:
            days = []
            for p in a.findall("forecast-period"):
                v = {e.get("type"): (e.text or "") for e in p}
                v["date"] = dt.date.fromisoformat(p.get("start-time-local")[:10])
                days.append(v)
            return days
    return []


def choices(key, options, fetch=False):
    """The web page's lists for the station and forecast town (CONFIG_SCHEMA "suggest"):
    [[value, label], ...] for the state in `options`, from the cached products (fetch=True:
    fetched if a day old or missing). [] when there is no state or no list yet."""
    st = STATES.get((options or {}).get("state"))
    if not st or key not in ("station", "forecast_area"):
        return []
    try:
        if key == "station":
            xml = product(st[1], cache_dir(), LIST_TTL, fetch)
            return [[s["wmo"], s["name"]] for s in stations(xml, st[3])] if xml else []
        xml = product(st[2], cache_dir(), LIST_TTL, fetch)
        return [[t["aac"], t["name"]] for t in towns(xml)] if xml else []
    except Exception as e:
        print(f"weather: no {key} list for {options.get('state')}: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------- drawing helpers
def vgrad(img, box, top, bot):
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0 - 1)
        d.line([(x0, y), (x1 - 1, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))


def stext(d, xy, text, f, fill=None, anchor="la", shadow=2):
    """Text with the hard black drop shadow that gives the 90s look."""
    fill = fill or WHITE                                 # the current theme's, not the day one's
    x, y = xy
    d.text((x + shadow, y + shadow), text, font=f, fill=SHADOW, anchor=anchor)
    d.text((x, y), text, font=f, fill=fill, anchor=anchor)


def wrap(text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def base_page(title1, title2, now):
    img = Image.new("RGB", (W, H))
    vgrad(img, (0, 0, W, H), SKY_TOP, SKY_BOT)
    vgrad(img, (0, 0, W, HEADER_H), HDR_TOP, HDR_BOT)
    d = ImageDraw.Draw(img)
    d.line([(0, HEADER_H), (W, HEADER_H)], fill=ORANGE, width=2)
    # logo box (top-left): tapping it returns to the Chumby UI
    logo = header_logo()
    img.paste(logo, (4, 3), logo)
    tx = 4 + logo.width + 8
    stext(d, (tx, 5), title1, font(15), YELLOW)
    stext(d, (tx, 21), title2, font(15), YELLOW)
    stext(d, (W - 6, 4), now.strftime("%a %b %-d").upper(), font(12), WHITE, anchor="ra", shadow=1)
    # ticker band
    d.rectangle([0, TICKER_Y - 2, W, TICKER_Y - 1], fill=ORANGE)
    vgrad(img, (0, TICKER_Y, W, H), TICK_TOP, TICK_BOT)
    return img


def panel(img, box):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=PANEL)
    d.rectangle([x0, y0, x1, y1], outline=ORANGE, width=2)


# ---------------------------------------------------------------- animated icons
def icon_kind(code):
    code = int(code or 3)
    return {1: "sun", 2: "moon", 3: "partly", 4: "cloud", 6: "fog", 8: "rain", 9: "wind",
            10: "fog", 11: "rain", 12: "rain", 13: "fog", 16: "storm", 17: "rain", 18: "rain"}.get(code, "partly")


def draw_cloud(d, cx, cy, s, fill=(235, 235, 245), edge=(150, 150, 175)):
    for dx, dy, r in ((-0.35, 0.05, 0.28), (0.0, -0.12, 0.36), (0.35, 0.05, 0.26), (0.0, 0.12, 0.3)):
        x, y, rr = cx + dx * s, cy + dy * s, r * s
        d.ellipse([x - rr - 1, y - rr - 1, x + rr + 1, y + rr + 1], fill=edge)
    for dx, dy, r in ((-0.35, 0.05, 0.28), (0.0, -0.12, 0.36), (0.35, 0.05, 0.26), (0.0, 0.12, 0.3)):
        x, y, rr = cx + dx * s, cy + dy * s, r * s
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=fill)


def draw_sun(d, cx, cy, s, phase):
    for i in range(12):
        a = phase + i * math.pi / 6
        r0, r1 = s * 0.42, s * (0.62 if i % 2 else 0.54)
        d.line([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                (cx + r1 * math.cos(a), cy + r1 * math.sin(a))], fill=(255, 190, 30), width=max(2, int(s / 16)))
    r = s * 0.34
    d.ellipse([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], fill=(230, 120, 10))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 215, 40))


def icon_frames(kind, size, n=8):
    """n frames, each size x size, keyed background. Supersampled 3x for smooth edges."""
    ss = 3
    frames = []
    for f in range(n):
        t = f / n
        im = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        S, c = size * ss, size * ss / 2
        if kind == "sun":
            draw_sun(d, c, c, S * 0.76, t * math.pi / 6 * 2)       # long rays reach 0.62 x size: keep inside the sprite
        elif kind == "moon":
            d.ellipse([c - S * .3, c - S * .3, c + S * .3, c + S * .3], fill=(240, 240, 200))
            d.ellipse([c - S * .15, c - S * .38, c + S * .38, c + S * .2], fill=(0, 0, 0, 0))
        elif kind == "partly":
            draw_sun(d, c - S * .1, c - S * .1, S * .6, t * math.pi / 3)
            draw_cloud(d, c + S * .1 + math.sin(t * 2 * math.pi) * S * .03, c + S * .16, S * .62)
        elif kind == "cloud":
            draw_cloud(d, c - S * .12 + math.sin(t * 2 * math.pi) * S * .04, c - S * .08, S * .55, (200, 200, 215))
            draw_cloud(d, c + S * .1 - math.sin(t * 2 * math.pi) * S * .04, c + S * .12, S * .65)
        elif kind in ("rain", "storm"):
            draw_cloud(d, c, c - S * .14, S * .75, (205, 205, 220))
            for i in range(5):
                x = c - S * .3 + i * S * .15
                y = c + S * .1 + ((t + i * .37) % 1) * S * .32
                d.line([(x, y), (x - S * .04, y + S * .1)], fill=(110, 190, 255), width=ss * 2)
            if kind == "storm" and f % 4 < 2:
                d.polygon([(c, c), (c - S * .1, c + S * .25), (c, c + S * .22), (c - S * .06, c + S * .45),
                           (c + S * .14, c + S * .14), (c + S * .03, c + S * .17)], fill=(255, 240, 60))
        elif kind == "fog":
            for i in range(4):
                y = c - S * .25 + i * S * .17
                off = math.sin(t * 2 * math.pi + i) * S * .06
                d.rounded_rectangle([c - S * .38 + off, y, c + S * .38 + off, y + S * .08], radius=S * .04, fill=(210, 210, 225))
        elif kind == "wind":
            for i in range(3):
                y = c - S * .2 + i * S * .2
                off = ((t + i * .3) % 1) * S * .2
                d.line([(c - S * .4 + off, y), (c + S * .25 + off, y)], fill=(230, 230, 240), width=ss * 3)
        # shrink in premultiplied alpha so no background colour bleeds into the edges,
        # then threshold to the device's colour key (it can't blend)
        sm = im.convert("RGBa").resize((size, size), Image.LANCZOS).convert("RGBA")
        im = Image.new("RGB", (size, size), KEY)
        im.paste(sm.convert("RGB"), (0, 0), sm.getchannel("A").point(lambda a: 255 if a >= 110 else 0))
        frames.append(im)
    sheet = Image.new("RGB", (size, size * n))
    for i, fr in enumerate(frames):
        sheet.paste(fr, (0, i * size))
    return sheet


# ---------------------------------------------------------------- pages
def page_current(obs, fc, now):
    img = base_page("Current", "Conditions", now)
    panel(img, (8, 48, W - 9, 208))
    d = ImageDraw.Draw(img)
    temp = obs.get("air_temp")
    stext(d, (72, 58), LOC["name"], font(18), YELLOW, anchor="ma")
    stext(d, (72, 78), f"{temp:.0f}°" if temp is not None else "--", font(44), WHITE, anchor="ma")
    # BOM often reports "-" for both; fall back to today's forecast wording
    cond = next((c for c in (obs.get("weather"), obs.get("cloud")) if c and c != "-"), "")
    if not cond and fc:
        cond = fc[0].get("precis", "").rstrip(".")
    stext(d, (72, 176), cond[:14], font(14), WHITE, anchor="ma")
    rows = [("Feels like", f"{obs.get('apparent_t', '--')}°"),
            ("Humidity", f"{obs.get('rel_hum', '--')}%"),
            ("Wind", f"{obs.get('wind_dir', '')} {obs.get('wind_spd_kmh', '--')} km/h"),
            ("Gusts", f"{obs.get('gust_kmh', '--')} km/h"),
            ("Pressure", f"{obs.get('press', '--')} hPa"),
            ("Rain 9am", f"{obs.get('rain_trace', '--')} mm")]
    for i, (k, v) in enumerate(rows):
        y = 60 + i * 22
        stext(d, (150, y), k, font(13), YELLOW, shadow=1)
        stext(d, (W - 16, y), v, font(13), WHITE, anchor="ra", shadow=1)
    t = obs.get("time", "")
    when = t[11:16] if len(t) >= 16 else ""
    hilo = f"  hi {obs['max']}° lo {obs['min']}°" if obs.get("max") else ""
    d.text((72, 196), f"obs {when}{hilo}", font=font(9), fill=FAINT, anchor="ma")
    code = fc[0].get("forecast_icon_code") if fc else (1 if cond.lower() in ("clear", "sunny") else 3)
    return img, [(icon_kind(code), 44, 50, 128)]


def page_local(fc, now):
    img = base_page("Local", "Forecast", now)
    d = ImageDraw.Draw(img)
    icons = []
    for i, day in enumerate(fc[:3]):
        x0 = 8 + i * 102
        panel(img, (x0, 48, x0 + 98, 208))
        name = "TODAY" if day["date"] == now.date() else day["date"].strftime("%A").upper()
        stext(d, (x0 + 49, 53), name[:9], font(13), YELLOW, anchor="ma", shadow=1)
        icons.append((icon_kind(day.get("forecast_icon_code")), 48, x0 + 25, 70))
        hi, lo = day.get("air_temperature_maximum", "--"), day.get("air_temperature_minimum", "")
        stext(d, (x0 + 49, 122), f"{hi}°" + (f" / {lo}°" if lo else ""), font(16), WHITE, anchor="ma")
        for j, line in enumerate(wrap(day.get("precis", ""), font(11), 88)[:3]):
            stext(d, (x0 + 49, 146 + j * 14), line, font(11), WHITE, anchor="ma", shadow=1)
        pop = day.get("probability_of_precipitation", "")
        if pop:
            d.text((x0 + 49, 196), f"rain {pop}", font=font(10), fill=RAIN, anchor="ma")
    return img, icons


def page_extended(fc, now):
    img = base_page("Extended", "Forecast", now)
    panel(img, (8, 48, W - 9, 208))
    d = ImageDraw.Draw(img)
    for i, day in enumerate(fc[1:7]):
        y = 54 + i * 25
        stext(d, (18, y + 3), day["date"].strftime("%a").upper(), font(14), YELLOW, shadow=1)
        small = icon_frames(icon_kind(day.get("forecast_icon_code")), 22, 1)
        img.paste(small, (58, y), _keymask(small))
        stext(d, (88, y + 4), day.get("precis", "")[:24], font(12, NARROW), WHITE, shadow=1)
        hi, lo = day.get("air_temperature_maximum", "--"), day.get("air_temperature_minimum", "--")
        stext(d, (W - 18, y + 3), f"{lo}° / {hi}°", font(14), WHITE, anchor="ra", shadow=1)
    return img, []


def _keymask(im):
    m = Image.new("L", im.size, 255)
    pm, pi = m.load(), im.load()
    for y in range(im.size[1]):
        for x in range(im.size[0]):
            if pi[x, y] == KEY:
                pm[x, y] = 0
    return m


# ---------------------------------------------------------------- clock + ticker
def clock_cells(now):
    """Glyphs drawn on the exact header rows they will sit on, so no keying is needed.
    Each glyph is left-aligned in its cell; widths go in the manifest so the
    device can lay the time out proportionally and right-align it."""
    glyphs = "0123456789: APM"
    f = font(14)
    widths = [int(round(f.getlength(g))) + 1 for g in glyphs]
    cw, ch = max(widths) + 1, 16
    base = base_page("", "", now).crop((W - cw, CLOCK_Y, W, CLOCK_Y + ch))   # header rows are uniform across x
    sheet = Image.new("RGB", (cw, ch * len(glyphs)))
    for i, g in enumerate(glyphs):
        cell = base.copy()
        stext(ImageDraw.Draw(cell), (0, 0), g, f, WHITE, shadow=1)
        sheet.paste(cell, (0, i * ch))
    return sheet, cw, ch, widths


def ticker_parts(obs, fc):
    """The weather pages' crawl: weather only (news lives in the news apps)."""
    parts = [f"{LOC['name'].upper()} NOW {obs.get('air_temp', '--')}°C  {obs.get('wind_dir', '')} {obs.get('wind_spd_kmh', '--')} km/h",
             f"HUMIDITY {obs.get('rel_hum', '--')}%  GUSTS {obs.get('gust_kmh', '--')} km/h"]
    for k, day in enumerate(fc[:3]):
        name = "TODAY" if k == 0 else day["date"].strftime("%A").upper()
        lo, hi = day.get("air_temperature_minimum"), day.get("air_temperature_maximum")
        lo, hi = (None if v in ("", "--", None) else v for v in (lo, hi))   # BOM drops today's max at night
        temps = f"  {lo}° / {hi}°" if lo and hi else f"  MAX {hi}°" if hi else f"  MIN {lo}°" if lo else ""
        parts.append(f"{name}: {day.get('precis', '').rstrip('.').upper()}{temps}")
    return parts


def ticker_strip(parts):
    f = font(14)
    text = "     •     ".join(parts) + "     •     "
    wpx = int(f.getlength(text)) + W
    img = Image.new("RGB", (wpx, TICKER_H))
    vgrad(img, (0, 0, wpx, TICKER_H), TICK_TOP, TICK_BOT)
    stext(ImageDraw.Draw(img), (W, 4), text, f, WHITE, shadow=1)
    return img


def page_setup(url, problem, now):
    """Until a place is chosen: where to choose it, with a QR code of the web page."""
    from .. import qrscreen
    img = base_page("Choose your", "location", now)
    panel(img, (8, 48, W - 9, 208))
    d = ImageDraw.Draw(img)
    lines = ([problem, ""] if problem else []) + [
        "Choose your state and the", "weather station nearest", "you: the Weather app's", "\"customize\" on the web page:"]
    y = 58 if not problem else 54
    for ln in lines:
        stext(d, (18, y), ln, font(13), YELLOW if problem and ln == problem else WHITE, shadow=1)
        y += 18 if ln else 6
    addr = re.sub(r"^https?://", "", url or "").rstrip("/")
    if addr:
        f = fit_font(addr, NARROW, 196, 14, 9)
        stext(d, (18, 188), addr, f, YELLOW, shadow=1)
        q, ok = qrscreen.qr_image(url, 92)
        if ok:                                    # no QR encoder: the address alone will do
            img.paste(q, (W - 9 - 8 - q.width, 56))
    return img


def no_forecast(img):
    """Over a forecast page's empty panels: the forecast town wasn't found."""
    d = ImageDraw.Draw(img)
    stext(d, (W // 2, 110), "No forecast town for this station", font(14), YELLOW, anchor="ma")
    stext(d, (W // 2, 132), "Pick one on the web page.", font(13), WHITE, anchor="ma")
    return img


# the sub-slides: id -> Home title, renderer
WEATHER_SLIDES = {
    "current": ("Current Conditions", lambda obs, fc, now: page_current(obs, fc, now)),
    "local": ("Local Forecast", lambda obs, fc, now: page_local(fc, now) if fc else (no_forecast(page_local(fc, now)[0]), [])),
    "extended": ("Extended Forecast",
                 lambda obs, fc, now: page_extended(fc, now) if fc else (no_forecast(page_extended(fc, now)[0]), [])),
}


def place(options, cache):
    """(obs, fc, station) for the chosen place, or (None, None, why) if there isn't one yet."""
    st = STATES.get(options.get("state"))
    if not st:
        return None, None, None
    wmo = options.get("station") or ""
    if not wmo:
        return None, None, None
    obs, station = get_obs(product(st[1], cache), wmo)
    if obs is None:
        return None, None, f"Station {wmo} isn't in {options['state']}'s list."
    xml = product(st[2], cache)
    aac = options.get("forecast_area") or (match_town(station, towns(xml)) or {}).get("aac")
    return obs, get_forecast(xml, aac) if aac else [], station


def render(ctx):
    """API 1: the pages ctx.slides asks for (or, with no place chosen yet, a page saying where
    to choose it), plus the clock and the ticker."""
    if not ctx.slides:
        return Rendered()
    now = ctx.now
    obs, fc, station = place(ctx.options, ctx.state_dir)
    p = ctx.first_page
    if obs is None:
        LOC["name"] = ""
        r = Rendered(home_title="Weather")
        img = page_setup(ctx.config_url, station, now)
        ctx.write565(img, "weather_setup.raw")
        set_theme("night")
        nimg = page_setup(ctx.config_url, station, now)
        set_theme("day")
        ctx.write565(nimg, "weather_setup_night.raw")
        r.lines += [f"page {p} weather_setup.raw {ctx.seconds}", f"pagenight {p} weather_setup_night.raw"]
        r.titles.append("Choose your location")
        r.thumbs.append(img)
        parts = ["WEATHER: CHOOSE YOUR LOCATION ON THE WEB PAGE"]
    else:
        LOC["name"] = ctx.options.get("name") or short_name(station["name"])
        r = Rendered(home_title=f"{LOC['name']} Weather")
        if station.get("lat") is not None:
            r.latlon = (station["lat"], station["lon"])
        for sid, _pages in ctx.slides:
            title, draw = WEATHER_SLIDES[sid]
            img, icons = draw(obs, fc, now)
            name = f"weather_{sid}"                        # stable per sub-slide, whatever the play order
            ctx.write565(img, f"{name}.raw")
            r.lines.append(f"page {p} {name}.raw {ctx.seconds}")
            set_theme("night")                             # the dark copy the engine draws in night mode
            nimg, _ = draw(obs, fc, now)
            set_theme("day")
            ctx.write565(nimg, f"{name}_night.raw")
            r.lines.append(f"pagenight {p} {name}_night.raw")
            for k, (kind, size, x, y) in enumerate(icons):
                frames = 8
                ctx.write565(icon_frames(kind, size, frames), f"{name}_icon{k}.raw")
                r.lines.append(f"sprite {p} {name}_icon{k}.raw {size} {size} {frames} {x} {y} 8")
            r.titles.append(title)
            r.thumbs.append(img)
            p += 1
        parts = ticker_parts(obs, fc)
        try:
            r.facts["feed"] = "weather of " + dt.datetime.fromisoformat(obs["time"]).strftime("%-d %b %Y, %-I:%M %p")
        except (KeyError, TypeError, ValueError):
            pass
    clk, cw, ch, widths = clock_cells(now)
    ctx.write565(clk, "clock.raw")
    r.lines.append(f"clock clock.raw {cw} {ch} {CLOCK_R} {CLOCK_Y} " + " ".join(map(str, widths)))
    tk = ticker_strip(parts)
    ctx.write565(tk, "ticker.raw")
    r.lines.append(f"ticker ticker.raw {tk.size[0]} {TICKER_H} {TICKER_Y} 2")
    # the same glyphs and strip on the night header and ticker band (same sizes: the engine
    # checks), used on the pages drawn from their pagenight
    set_theme("night")
    nclk, ncw, nch, nwidths = clock_cells(now)
    ntk = ticker_strip(parts)
    set_theme("day")
    if (ncw, nch, nwidths) == (cw, ch, widths) and ntk.size == tk.size:
        ctx.write565(nclk, "clock_night.raw")
        ctx.write565(ntk, "ticker_night.raw")
        r.lines += ["clocknight clock_night.raw", "tickernight ticker_night.raw"]
    return r
