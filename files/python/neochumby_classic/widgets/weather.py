"""weather: the Weather widget, a 90s-weather-channel look for the Bureau of Meteorology's data.

Pages (each with a dark copy for night mode):
  weather_current, weather_local, weather_extended   320x240 page backgrounds, clock area left blank
  weather_<page>_icon<N>                             animated icon sprite sheets (frames stacked
                                                     vertically, 0xF81F = transparent)
and, for the whole slideshow, the clock and the ticker the engine draws over these pages:
  clock.raw, clock_night.raw     glyph cells for "0123456789: APM", on the header background
  ticker.raw, ticker_night.raw   a long 24 px strip the engine scrolls: the weather now, today's
                                 forecast and (optionally) some headlines

BOM's website blocks scrapers and points to its anonymous FTP, so observations and forecasts
come from there, by product id (the options below).
"""
import datetime as dt
import math
import sys
import urllib.request
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from .. import assets, config
from ..widget import Rendered

API = 1
APP = {"id": "weather", "name": "Weather", "icon": "icons/weather.png", "seconds": 12,
       "description": "Weather from the Bureau of Meteorology for the place you set, with the scrolling news ticker.",
       "subslides": [{"id": "current", "label": "Current Conditions", "default": True},
                     {"id": "local", "label": "Local Forecast", "default": True},
                     {"id": "extended", "label": "Extended Forecast", "default": True}]}

# Whyalla, South Australia, is only the example default: any Australian place with a BOM
# observation station and a town forecast works. The ids are in BOM's anonymous FTP products
# (ftp://ftp.bom.gov.au/anon/gen/fwo/): the state's observations product names each
# station's wmo-id, and its town forecast product each place's aac.
CONFIG_SCHEMA = [
    {"key": "location", "type": "string", "label": "Place name", "default": "Whyalla", "max_len": 20,
     "pattern": r"[A-Za-z][A-Za-z .'\-]{0,19}", "help": "Shown on the pages and the ticker."},
    {"key": "obs_product", "type": "string", "label": "BOM observations product", "default": "IDS60920",
     "max_len": 8, "pattern": r"ID[A-Z][0-9]{5}",
     "help": "The state's observations XML (South Australia: IDS60920; New South Wales: IDN60920)."},
    {"key": "obs_station", "type": "string", "label": "Observation station (wmo-id)", "default": "95664",
     "max_len": 5, "pattern": r"[0-9]{5}", "help": "The station's wmo-id in that product."},
    {"key": "forecast_product", "type": "string", "label": "BOM town forecasts product", "default": "IDS10044",
     "max_len": 8, "pattern": r"ID[A-Z][0-9]{5}", "help": "The state's town forecasts XML (South Australia: IDS10044)."},
    {"key": "forecast_area", "type": "string", "label": "Forecast area (aac)", "default": "SA_PT055",
     "max_len": 10, "pattern": r"[A-Z]{2,3}_[A-Z]{2}[0-9]{3}", "help": "The place's aac in that product."},
    {"key": "ticker_headlines", "type": "bool", "label": "Headlines in the ticker", "default": True,
     "help": "ABC News headlines after the weather in the scrolling ticker."},
]

W, H = 320, 240
UA = config.USER_AGENT
# The place: the widget's options, applied by set_location() before anything is drawn.
BOM_FTP = "ftp://ftp.bom.gov.au/anon/gen/fwo/{}.xml"
LOC = {}
# The ticker's headlines (examples: ABC Adelaide and ABC Just In)
NEWS_URLS = ["https://www.abc.net.au/news/feed/8057540/rss.xml",
             "https://www.abc.net.au/news/feed/51120/rss.xml"]
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


def set_location(opts):
    """The widget's options (CONFIG_SCHEMA) -> LOC, what the pages read."""
    o = dict(config.defaults(CONFIG_SCHEMA), **(opts or {}))
    LOC.update(name=o["location"], obs_url=BOM_FTP.format(o["obs_product"]), wmo=o["obs_station"],
               fcst_url=BOM_FTP.format(o["forecast_product"]), aac=o["forecast_area"])


set_location(None)


def badge():
    """The weather pages' badge when no weather-badge.png is supplied: a sun behind a
    cloud on a navy tile with an orange rim, drawn at 4x and shrunk."""
    k, w, h = 4, 120, 96
    im = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([2 * k, 2 * k, (w - 2) * k, (h - 2) * k], radius=16 * k, fill=(30, 40, 120), outline=(230, 120, 20),
                        width=5 * k)
    draw_sun(d, 48 * k, 40 * k, 52 * k, 0.3)
    draw_cloud(d, 70 * k, 58 * k, 56 * k)
    return im.resize((w, h), Image.LANCZOS)


def header_logo(h=34):
    """The badge, dimmed in the night theme (its white and yellow would glare)."""
    if THEME not in _logo:
        path = assets.find("weather-badge.png")
        src = Image.open(path).convert("RGBA") if path else badge()
        im = src.resize((round(src.width * h / src.height), h), Image.LANCZOS)
        if LOGO_DIM != 1.0:
            r, g, b, a = im.split()
            r, g, b = (c.point(lambda v: int(v * LOGO_DIM)) for c in (r, g, b))
            im = Image.merge("RGBA", (r, g, b, a))
        _logo[THEME] = im
    return _logo[THEME]
CLOCK_R, CLOCK_Y = W - 6, 22   # the device right-aligns the time to CLOCK_R


def font(size, path=BOLD):
    return ImageFont.truetype(path, size)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ---------------------------------------------------------------- data
def get_obs():
    """The location's latest observation, mapped to the keys the pages use."""
    root = ET.fromstring(fetch(LOC["obs_url"]))
    st = next(s for s in root.iter("station") if s.get("wmo-id") == LOC["wmo"])
    per = next(st.iter("period"))
    e = {x.get("type"): x.text for x in per.iter("element")}
    num = lambda k: float(e[k]) if e.get(k) not in (None, "") else None
    return {"air_temp": num("air_temperature"), "apparent_t": e.get("apparent_temp", "--"),
            "rel_hum": e.get("rel-humidity", "--"), "wind_dir": e.get("wind_dir", ""),
            "wind_spd_kmh": e.get("wind_spd_kmh", "--"), "gust_kmh": e.get("gust_kmh", "--"),
            "press": e.get("msl_pres", "--"), "rain_trace": e.get("rainfall", "--"),
            "weather": e.get("weather"), "cloud": e.get("cloud"),
            "max": e.get("maximum_air_temperature"), "min": e.get("minimum_air_temperature"),
            "time": per.get("time-local", "")}


def get_forecast():
    root = ET.fromstring(fetch(LOC["fcst_url"]))
    for a in root.iter("area"):
        if a.get("aac") == LOC["aac"]:
            days = []
            for p in a.findall("forecast-period"):
                v = {e.get("type"): (e.text or "") for e in p}
                v["date"] = dt.date.fromisoformat(p.get("start-time-local")[:10])
                days.append(v)
            return days
    return []


def get_news(limit=8):
    seen, out = set(), []
    for url in NEWS_URLS:
        try:
            ch = ET.fromstring(fetch(url)).find("channel")
        except Exception as e:  # one dead feed must not kill the page
            print("news feed failed:", url, e, file=sys.stderr)
            continue
        for it in ch.findall("item"):
            t = (it.findtext("title") or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out[:limit]


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


def ticker_strip(obs, fc, news):
    f = font(14)
    parts = [f"{LOC['name'].upper()} NOW {obs.get('air_temp', '--')}°C  {obs.get('wind_dir', '')} {obs.get('wind_spd_kmh', '--')} km/h"]
    if fc:
        parts.append(f"TODAY: {fc[0].get('precis', '')} Max {fc[0].get('air_temperature_maximum', '--')}°")
    parts += [h.upper() for h in news]
    text = "     •     ".join(parts) + "     •     "
    wpx = int(f.getlength(text)) + W
    img = Image.new("RGB", (wpx, TICKER_H))
    vgrad(img, (0, 0, wpx, TICKER_H), TICK_TOP, TICK_BOT)
    stext(ImageDraw.Draw(img), (W, 4), text, f, WHITE, shadow=1)
    return img


# the sub-slides: id -> Home title, renderer
WEATHER_SLIDES = {
    "current": ("Current Conditions", lambda obs, fc, now: page_current(obs, fc, now)),
    "local": ("Local Forecast", lambda obs, fc, now: page_local(fc, now)),
    "extended": ("Extended Forecast", lambda obs, fc, now: page_extended(fc, now)),
}



def render(ctx):
    """API 1: the pages ctx.slides asks for, plus the clock and the ticker."""
    if not ctx.slides:
        return Rendered()
    set_location(ctx.options)
    now = ctx.now
    obs, fc = get_obs(), get_forecast()
    news = get_news() if ctx.options.get("ticker_headlines", True) else []
    r = Rendered(home_title=f"{LOC['name']} Weather")
    p = ctx.first_page
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
    clk, cw, ch, widths = clock_cells(now)
    ctx.write565(clk, "clock.raw")
    r.lines.append(f"clock clock.raw {cw} {ch} {CLOCK_R} {CLOCK_Y} " + " ".join(map(str, widths)))
    tk = ticker_strip(obs, fc, news)
    ctx.write565(tk, "ticker.raw")
    r.lines.append(f"ticker ticker.raw {tk.size[0]} {TICKER_H} {TICKER_Y} 2")
    # the same glyphs and strip on the night header and ticker band (same sizes: the engine
    # checks), used on the pages drawn from their pagenight
    set_theme("night")
    nclk, ncw, nch, nwidths = clock_cells(now)
    ntk = ticker_strip(obs, fc, news)
    set_theme("day")
    if (ncw, nch, nwidths) == (cw, ch, widths) and ntk.size == tk.size:
        ctx.write565(nclk, "clock_night.raw")
        ctx.write565(ntk, "ticker_night.raw")
        r.lines += ["clocknight clock_night.raw", "tickernight ticker_night.raw"]
    try:
        r.facts["feed"] = "weather of " + dt.datetime.fromisoformat(obs["time"]).strftime("%-d %b %Y, %-I:%M %p")
    except (KeyError, TypeError, ValueError):
        pass
    return r
