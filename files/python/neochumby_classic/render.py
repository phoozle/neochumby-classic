"""render: one render of one Chumby's channel, into its output directory.

The Chumby (ARM926, no FPU) only copies pixels, so everything that is hard (fetching,
parsing, fonts, anti-aliasing, layout) happens here, on the server. Each widget in the
Chumby's channel draws its own pages and screens (widget.py, docs/widgets.md); this module
adds what belongs to no widget (Home, Settings, APPS, the sun times for night mode),
checks what the widgets wrote, and finishes the directory. docs/interface.md describes
the result: manifest.txt, the RGB565 and L8 files it names, hashes.txt and version.

Usage: neochumby-classic-render OUTDIR [--config FILE] [--chumby NAME] [--info FILE] [--player APP]

Every file is written only if its bytes changed, and atomically, so a reader never sees
half a file; a render that changes nothing writes nothing, manifest and version included.
--info names a key=value file of facts about the Chumby (ssid, ip, firmware, ...) for
Settings -> Network and Chumby Info; without it they show "?".
"""
import argparse
import datetime as dt
import hashlib
import math
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from . import admin, appsscreen, assets, config, limits, output, qrscreen, screens, widget

W, H = widget.W, widget.H
FILE_OK = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,%d}" % (limits.FILE_NAME - 1))
SCREEN_OK = re.compile(r"[A-Za-z0-9_]{1,%d}" % limits.SCREEN_NAME)   # "_night" included
PRUNE_AFTER = 3600            # unnamed files and stale temp files older than this are removed
# The engine's limits (limits.py, docs/widgets.md). A render over one is refused, whole.
LIMITS = limits.LIMITS
PAGE_BUDGET = 2 * 1024 * 1024  # bytes one page or screen may need in memory at once
# Directives a widget may write (docs/widgets.md). The rest belong to this module.
WIDGET_DIRECTIVES = {"page", "pagenight", "pagetap", "sprite", "actor", "screen", "pane", "hot", "slider",
                     "logofx", "scroller", "bgscroll", "glow", "glowseq", "clock", "clocknight", "ticker",
                     "tickernight", "livefont", "live", "clip", "flurry"}
LIVE_KEYS, CHARSET = widget.LIVE_KEYS, widget.LIVE_CHARSET
ONCE = {"clock", "clocknight", "ticker", "tickernight"}     # global: the first widget's wins


# ---------------------------------------------------------------- sunrise / sunset
def sun_events(day, lat, lon):
    """(sunrise, sunset) as UTC datetimes for a local date at (lat, lon): NOAA's solar
    equations, zenith 90.833 deg (refraction + the sun's radius). Good to a minute or so."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    ha = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))      # clamped: polar day or night
    midnight = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    rise = midnight + dt.timedelta(minutes=720 - 4 * (lon + ha) - eqt)
    sets = midnight + dt.timedelta(minutes=720 - 4 * (lon - ha) - eqt)
    return rise, sets


def sun_lines(now, lat, lon, days=8):
    """'sun RISE SET' (epoch seconds) for the next week, so night mode keeps working on an
    old copy even if the renderer stops."""
    out = []
    for i in range(days):
        rise, sets = sun_events(now.date() + dt.timedelta(days=i), lat, lon)
        out.append(f"sun {int(rise.timestamp())} {int(sets.timestamp())}")
    return out


# ---------------------------------------------------------------- Settings -> Chumby Info
# The keys of <state>/info/<chumby> (docs/interface.md, "Facts from the Chumby"): what the
# Chumby can't read itself. The rest (its name, firmware, uptime, memory, USB stick, network,
# IP address and signal) the engine reads on the Chumby and draws live (screens.py). Other
# keys are ignored, and a missing or empty one shows "?".
INFO_KEYS = ("ap", "rate", "signal", "mac", "gateway", "dns", "kernel", "engine", "asof")


def chumby_info(path, facts):
    """Facts about the Chumby for Settings -> Network and Chumby Info that it can't read
    itself, from a key=value file with the INFO_KEYS, plus the widgets' facts (the weather's
    observation time, as Feed). neochumby's companion is to write that file; nothing writes
    it yet. Without it, or a key, the row shows "?". No fact may change on every render:
    that would make the screen new every time."""
    info = dict(facts)
    if not path:
        return info
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = dict(ln.rstrip("\n").split("=", 1) for ln in fh if "=" in ln)
    except OSError as e:
        print(f"chumby info: {e}", file=sys.stderr)
        return info
    raw = {k.strip(): "".join(ch for ch in v if ch.isprintable()).strip()[:80] for k, v in raw.items()}
    raw = {k: v for k, v in raw.items() if k in INFO_KEYS and v}
    if not re.fullmatch(r"-?[0-9]{1,3}", raw.get("signal", "")):   # dBm
        raw.pop("signal", None)
    info.update(raw)
    if "kernel" in info:
        info["kernel"] = f"Linux {info['kernel']}"
    return info


def empty_page():
    """The only page, when no widget has one: where to add some."""
    img = Image.new("RGB", (W, H), (28, 10, 87))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(assets.FONTS + "DejaVuSans-Bold.ttf", 16)
    d.text((W // 2, 100), "Nothing to show yet", font=f, fill=(255, 230, 60), anchor="ma")
    d.text((W // 2, 126), "Add apps on the web page.", font=f, fill=(240, 240, 240), anchor="ma")
    return img


# ---------------------------------------------------------------- checking a widget's lines
class WidgetError(Exception):
    pass


def check_lines(app, r, first, out):
    """Raises WidgetError if a widget's result breaks the contract (docs/widgets.md)."""
    pages = [ln.split() for ln in r.lines if ln.split()[:1] == ["page"]]
    if len(pages) != len(r.titles) or len(r.thumbs) != len(r.titles):
        raise WidgetError(f"{app}: {len(pages)} pages, {len(r.titles)} titles, {len(r.thumbs)} thumbnails")
    for k, t in enumerate(pages):
        if len(t) < 4 or t[1] != str(first + k):
            raise WidgetError(f"{app}: page lines must number {first}.. in order: {' '.join(t)}")
    for ln in r.lines:
        t = ln.split()
        if not t or t[0].startswith("#"):                # comments: notes for people, ignored by the engine
            continue
        if t[0] not in WIDGET_DIRECTIVES:
            raise WidgetError(f"{app}: directive {t[0]!r} isn't one a widget may write")
        if t[0] in ("pagenight", "pagetap", "sprite", "actor", "logofx", "scroller", "bgscroll", "glow", "glowseq", "clip",
                    "flurry"):
            if len(t) < 3 or not t[1].isdigit() or not first <= int(t[1]) < first + len(pages):
                raise WidgetError(f"{app}: {t[0]} for a page that isn't its own: {ln}")
        m = re.fullmatch(r"page(?:night)?:(\d+)", t[1]) if t[0] in ("pane", "live") and len(t) > 1 else None
        if m and not first <= int(m.group(1)) < first + len(pages):
            raise WidgetError(f"{app}: {t[0]} for a page that isn't its own: {ln}")
        if "\n" in ln or len(ln) > 1000:
            raise WidgetError(f"{app}: bad line")
        names = [t[1]] if t[0] in ("screen", "pane", "hot", "slider", "live") and len(t) > 1 and not m else []
        names += [t[2]] if t[0] == "pagetap" and len(t) > 2 else []
        names += [t[-1]] if t[0] == "hot" and len(t) > 2 and t[-2] == "screen" else []
        bad = [n for n in names if not SCREEN_OK.fullmatch(n)]
        if bad:
            raise WidgetError(f"{app}: screen name {bad[0]!r} isn't 1-{limits.SCREEN_NAME} letters, digits or '_'")
        for tok in t + clip_files(t):
            if tok.endswith((".raw", ".l8", ".path", ".idx", ".clip")):
                if not FILE_OK.fullmatch(tok):
                    raise WidgetError(f"{app}: unsafe file name {tok!r}")
                if not os.path.isfile(os.path.join(out, tok)):
                    raise WidgetError(f"{app}: names {tok}, which it didn't write")


def check_limits(lines, out=None):
    """Raises WidgetError if the manifest is over one of the engine's limits (limits.py), or
    has a line the engine would skip: names too long, a pane or live box for a screen or page
    not declared before it, a font or box out of bounds. With `out`, the files' sizes and
    the number of different files are checked too."""
    counts, over = {}, []
    for ln in lines:
        k = ln.split(" ", 1)[0]
        counts[k] = counts.get(k, 0) + 1
    over += [f"{k} {counts[k]} > {n}" for k, n in LIMITS.items() if counts.get(k, 0) > n]
    over += problems(lines, out)
    if out is not None:
        n = len(referenced_files(out, lines))
        if n > limits.ASSETS:
            over.append(f"files {n} > {limits.ASSETS}")
    if over:
        raise WidgetError("over the engine's limits: " + ", ".join(over[:10]))


def _size(out, name):
    try:
        return os.path.getsize(os.path.join(out, name))
    except OSError:
        return None


FLURRY_PRESETS = {"classic", "rgb", "water", "fire", "psychedelic", "random"}
FLURRY_COLOURS = {"red", "magenta", "blue", "cyan", "green", "yellow", "slowcyclic", "cyclic", "tiedye", "rainbow",
                  "white", "multi", "dark"}


def flurry_words_ok(words):
    """Whether the engine takes a `flurry` line's words (flurry::Options::parse): a preset
    first, then KEY=VALUE options in range. An unknown key is fine (a later engine's)."""
    def num(v, lo, hi):
        return v.isdigit() and lo <= int(v) <= hi

    def q8(v, lo, hi):          # "1.25" in 256ths, as the engine reads it
        m = re.fullmatch(r"(\d{1,7})(?:\.(\d{0,3})\d*)?", v)
        if not m:
            return False
        milli = int(m.group(1)) * 1000 + int((m.group(2) or "").ljust(3, "0"))
        return lo <= (milli * 256 + 500) // 1000 <= hi

    for i, w in enumerate(words):
        if "=" not in w:
            if i or w not in FLURRY_PRESETS:
                return False
            continue
        k, v = w.split("=", 1)
        ok = {"streams": lambda: num(v, 1, 16), "colour": lambda: v in FLURRY_COLOURS, "color": lambda: v in FLURRY_COLOURS,
              "speed": lambda: q8(v, 0, 1024), "thickness": lambda: num(v, 1, 20000), "bright": lambda: q8(v, 16, 1024),
              "seed": lambda: num(v, 0, 2**32 - 1), "puffs": lambda: num(v, 64, 3600), "rate": lambda: num(v, 10, 121)}
        if k in ok and not ok[k]():
            return False
    return True


def problems(lines, out=None):
    """The lines the engine would skip (engine-limits and live values, v1), as messages."""
    bad, screens_seen, pages, fonts = [], set(), 0, set()
    lo, hi = limits.COORD

    def name_ok(n):
        return bool(SCREEN_OK.fullmatch(n))

    def target_ok(tg):
        m = re.fullmatch(r"page(?:night)?:(\d+)", tg)
        return int(m.group(1)) < pages if m else tg in screens_seen

    for ln in lines:
        t = ln.split()
        if not t or t[0].startswith("#"):
            continue
        k = t[0]
        try:
            if k == "page":
                pages += 1
            elif k == "screen":
                if not name_ok(t[1]):
                    bad.append(f"screen name {t[1]!r}")
                screens_seen.add(t[1])
            elif k == "pagetap" and not name_ok(t[2]):
                bad.append(f"pagetap {t[2]!r}")
            elif k == "hot":
                if len(t[6]) > limits.ACTION or (len(t) > 7 and len(t[7]) > limits.SCREEN_NAME):
                    bad.append(f"hot {' '.join(t[6:])!r}")
                if not all(lo <= int(v) <= hi for v in t[2:6]):
                    bad.append(f"hot box {ln!r}")
            elif k == "glowseq" and len(t[2]) > limits.GLOWSEQ_PREFIX or (k == "glowseq" and int(t[3]) > limits.GLOWSEQ_FRAMES):
                bad.append(f"glowseq {t[2]}")
            elif k == "pane":
                w, h, x, y, frames = int(t[3]), int(t[4]), int(t[5]), int(t[6]), int(t[8])
                if not target_ok(t[1]) or len(t[7]) > limits.PANE_VAR or min(w, h, frames) < 1 \
                        or not all(lo <= v <= hi for v in (w, h, x, y)):
                    bad.append(f"pane {ln!r}")
                elif out is not None and _size(out, t[2]) != w * h * frames * 2:
                    bad.append(f"pane file {t[2]} isn't {w}x{h}x{frames}")
            elif k == "livefont":
                cw, ch, widths = int(t[3]), int(t[4]), t[5:]
                if not name_ok(t[1]) or not (1 <= cw <= 64 and 1 <= ch <= 64) or len(widths) not in (0, len(CHARSET)) \
                        or not all(w.isdigit() for w in widths):
                    bad.append(f"livefont {ln[:60]!r}")
                elif out is not None and _size(out, t[2]) != cw * ch * len(CHARSET) * 2:
                    bad.append(f"livefont file {t[2]} isn't {cw}x{ch * len(CHARSET)}")
                fonts.add(t[1])
            elif k == "flurry":
                if not t[1].isdigit() or int(t[1]) >= pages or not flurry_words_ok(t[2:]):
                    bad.append(f"flurry {ln[:60]!r}")
            elif k == "clip":
                names = [n for n in t[3:] if n != "random"]
                if not t[1].isdigit() or int(t[1]) >= pages or t[2] != "." or not names \
                        or "random" in t[3:-1] or not all(FILE_OK.fullmatch(n + ".clip") for n in names):
                    bad.append(f"clip {ln[:60]!r}")
            elif k == "live":
                x, y, w, h = map(int, t[4:8])
                if not target_ok(t[1]) or t[2] not in LIVE_KEYS or t[3] not in fonts or len(t) != 10 \
                        or w < 1 or h < 1 or x < 0 or y < 0 or x + w > W or y + h > H \
                        or t[8] not in ("left", "right", "center") or not re.fullmatch(r"bg|#[0-9A-Fa-f]{6}", t[9]):
                    bad.append(f"live {ln!r}")
        except (IndexError, ValueError):
            bad.append(f"malformed {ln[:60]!r}")
    return bad


def memory_use(out, lines):
    """{(kind, name): bytes} that each page and screen needs at once: its picture and the
    files drawn on it (sprites, panes, glows, sliders, three glowseq frames, ...)."""
    use = {}

    def add(key, f):
        try:
            use[key] = use.get(key, 0) + os.path.getsize(os.path.join(out, f))
        except OSError:
            pass
    for ln in lines:
        t = ln.split()
        if not t:
            continue
        if t[0] in ("page", "pagenight", "sprite", "actor", "logofx", "scroller", "bgscroll", "glow") and len(t) > 2:
            add(("page", t[1]), t[2])
        elif t[0] == "glowseq" and len(t) > 3 and t[3].isdigit():
            for k in range(min(3, int(t[3]))):
                add(("page", t[1]), f"{t[2]}_{k:03d}.l8")
        elif t[0] in ("screen", "pane") and len(t) > 2:
            add(("screen", t[1]), t[2])
        elif t[0] == "slider" and len(t) > 4:
            for f in t[2:5]:
                add(("screen", t[1]), f)
    return use


# ---------------------------------------------------------------- the manifest
def clip_files(t):
    """The files a `clip N DIR NAME... [random]` line names: NAME.idx and NAME.clip, for DIR "."."""
    if t[:1] != ["clip"] or len(t) < 4 or t[2] != ".":
        return []
    return [n + ext for n in t[3:] if n != "random" for ext in (".idx", ".clip")]


def referenced_files(out, lines):
    """The files the manifest names (glowseq frames by prefix). Stale renders left in OUT
    are not named, so they are never delivered."""
    names = set()
    for line in lines:
        toks = line.split()
        if len(toks) >= 4 and toks[0] == "glowseq" and toks[3].isdigit():
            toks = toks + [f"{toks[2]}_{k:03d}.l8" for k in range(int(toks[3]))]
        for tok in toks + clip_files(toks):
            if tok.endswith((".raw", ".path", ".l8", ".idx", ".clip")) and os.path.isfile(os.path.join(out, tok)):
                if not FILE_OK.fullmatch(tok):
                    raise ValueError(f"unsafe file name in manifest: {tok!r}")
                names.add(tok)
    return sorted(names)


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_version(out):
    """(version, digest) from OUT/version, or (0, "")."""
    try:
        with open(os.path.join(out, "version")) as fh:
            v, d = (fh.read(100).split() + ["", ""])[:2]
        return int(v), d
    except (OSError, ValueError):
        return 0, ""


def finish(out, lines, now):
    """Write hashes.txt, then manifest.txt, then version, unless nothing changed since the
    last render (docs/interface.md). The digest covers everything the manifest names and
    says, so a render that changes nothing leaves all three alone. Returns True if it wrote."""
    files = referenced_files(out, lines)
    hashes = "".join(f"{md5_file(os.path.join(out, f))}  {f}\n" for f in files)
    body = "\n".join(lines) + "\n"
    meaning = "\n".join(ln for ln in lines if not ln.startswith("#"))
    digest = hashlib.sha256((hashes + meaning).encode()).hexdigest()[:16]
    for name, data in (("hashes.txt", hashes), ("manifest.txt", body)):
        if len(data.encode()) + 100 > limits.MANIFEST_BYTES:
            raise WidgetError(f"{name} would be over {limits.MANIFEST_BYTES // 1048576} MB")
    old_version, old_digest = read_version(out)
    try:
        with open(os.path.join(out, "manifest.txt")) as fh:
            same = fh.readline().strip() == f"# classic {digest}"
    except OSError:
        same = False
    if same and old_digest == digest:
        return False
    version = max(int(now.timestamp()), old_version + 1)      # always goes up
    output.write_if_changed(os.path.join(out, "hashes.txt"), hashes.encode())
    output.write_if_changed(os.path.join(out, "manifest.txt"),
                            f"# classic {digest}\nversion {version}\n{body}".encode())
    output.write_if_changed(os.path.join(out, "version"), f"{version} {digest}\n".encode())
    return True


def prune(out, lines, older=PRUNE_AFTER):
    """Remove pictures the manifest no longer names, and temp files a crash left, once
    they're older than `older` seconds (a reader may still be on the last version)."""
    keep = set(referenced_files(out, lines))
    cutoff = time.time() - older
    for name in os.listdir(out):
        p = os.path.join(out, name)
        stale = name.startswith(".") and name.endswith(".tmp")
        stale = stale or (name.endswith((".raw", ".l8", ".path", ".png", ".idx", ".clip")) and name not in keep)
        try:
            if stale and os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.unlink(p)
        except OSError:
            pass


# ---------------------------------------------------------------- one render
def timezone(name):
    try:
        return ZoneInfo(name)
    except Exception:
        print(f"render: unknown timezone {name!r}, using {config.DEFAULT_TZ}", file=sys.stderr)
        return ZoneInfo(config.DEFAULT_TZ)


def render(out, cfg, chumby=None, info_path=None, now=None, player=None):
    """Render `chumby`'s channel into `out`. Returns True if anything changed. player: the
    Chumby's player= setting (from neochumbyd's paired list), for the star on APPS.
    A widget that raises fails the whole render: the last good output stays as it was."""
    os.makedirs(out, exist_ok=True)
    screens.T = screens.LIGHT                              # screens' theme is a module global (widgets draw with it too)
    tz = timezone(cfg["general"]["timezone"])
    now = (now or dt.datetime.now(tz)).astimezone(tz)
    cid = config.channel_of(cfg, chumby)
    slides, dropped = config.plan(cfg, cid)
    for s in dropped:
        print(f"over the engine's {config.MAXP} pages: dropped {s['app']} {s['subslide'] or ''} ({s['pages']} page(s))",
              file=sys.stderr)
    reg = {a["id"]: a for a in config.registry()}
    lines, titles, thumbs, page_app, facts, home_titles, index = [], [], [], [], {}, {}, {}
    latlon = None
    seen_once = set()
    entries = [e for e in config.channel(cfg, cid)["apps"] if e["enabled"] and e["app"] in reg]
    for e in entries:
        a = reg[e["app"]]
        mine = [s for s in slides if s["app"] == a["id"]]
        sdir = os.path.join(config.state_dir(), "widgets", a["id"])
        ctx = widget.Context(out=out, first_page=len(titles), slides=[(s["subslide"], s["pages"]) for s in mine],
                             seconds=e["seconds"], options=mine[0]["options"] if mine else e["options"], now=now,
                             tz=tz, chumby=chumby, state_dir=sdir, config_url=cfg["general"]["config_url"],
                             user_agent=config.USER_AGENT)
        r = a["module"].render(ctx)
        check_lines(a["id"], r, len(titles), out)
        for ln in r.lines:
            kind = ln.split(" ", 1)[0]
            if kind in ONCE:
                if kind in seen_once:
                    continue
                seen_once.add(kind)
            lines.append(ln)
        page_app += [a["id"]] * len(r.titles)
        titles += r.titles
        thumbs += r.thumbs
        facts.update({k: str(v)[:80] for k, v in (r.facts or {}).items()})
        if r.home_title:
            home_titles[a["id"]] = r.home_title
        if latlon is None and r.latlon:
            try:
                la, lo = map(float, r.latlon)
                latlon = (la, lo) if -90 <= la <= 90 and -180 <= lo <= 180 else None
            except (TypeError, ValueError):
                pass
        index[a["id"]] = [ln.split()[2] for ln in r.lines if ln.startswith("page ")]
    if not titles:
        img = empty_page()
        output.rgb565(img, os.path.join(out, "empty.raw"))
        lines.append("page 0 empty.raw 60 noclock noticker")
        titles, thumbs, page_app = ["Nothing to show"], [img], [""]
    g = cfg["general"]
    if g.get("latitude") is not None and g.get("longitude") is not None:
        latlon = (g["latitude"], g["longitude"])           # the owner's place wins over a widget's
    man = sun_lines(now, *latlon) if latlon else []        # no place yet: night mode can't follow the sun
    man += lines
    # Home browses apps, not pages: number the apps in play order and tell the engine each page's app
    apps = []                                              # (name, first page)
    for pg, aid in enumerate(page_app):
        if not apps or page_app[pg - 1] != aid:
            name = home_titles.get(aid) or (reg[aid]["name"] if aid in reg else titles[pg])
            apps.append((name, pg))
        man.append(f"pageapp {pg} {len(apps) - 1}")
    config.write_render_index(out, index)
    # Home (top button), Settings and APPS
    app_titles, app_thumbs = [n for n, _ in apps], [thumbs[pg] for _, pg in apps]
    write = output.rgb565
    info = chumby_info(info_path, facts)
    man += screens.home(out, write, app_titles, app_thumbs)
    man += screens.home(out, write, app_titles, app_thumbs, night=True)    # dark theme, shown in night mode
    man += screens.settings(out, write, info)
    man += screens.settings(out, write, info, night=True)
    apps = appsscreen.full_apps()                          # Home -> APPS: the neochumby apps in the tree
    fav = admin.default_player(player, [(a["name"], a["title"]) for a in apps if a["player"]])
    man += appsscreen.render(out, write, apps, default=fav)
    man += appsscreen.render(out, write, apps, night=True, default=fav)
    url = g["config_url"]                                  # Settings -> WEB PAGE: the QR code for the web page
    man += qrscreen.configure(out, write, url)
    man += qrscreen.configure(out, write, url, night=True)
    screens.T = screens.LIGHT
    check_limits(man, out)
    for (kind, name), n in sorted(memory_use(out, man).items()):
        if n > PAGE_BUDGET:
            print(f"render: {kind} {name} needs {n // 1024} KB at once, over the {PAGE_BUDGET // 1024} KB budget",
                  file=sys.stderr)
    changed = finish(out, man, now)
    prune(out, man)
    print(f"rendered {len(titles)} pages for {chumby or 'a Chumby'} into {out}"
          + ("" if changed else " (nothing changed)"), file=sys.stderr)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="neochumby-classic-render", description="Render one Chumby's channel once.")
    ap.add_argument("out", help="the Chumby's output directory")
    ap.add_argument("--config", default=None, help="the config file (default: see config.default_path)")
    ap.add_argument("--chumby", default=None, help="the Chumby's name (picks its channel)")
    ap.add_argument("--info", default=None, help="a key=value file of facts about the Chumby")
    ap.add_argument("--player", default=None, help="the Chumby's player setting (neochumbyd's player=)")
    a = ap.parse_args(argv)
    if a.chumby is not None and not re.fullmatch(config.CHUMBY_NAME, a.chumby):
        ap.error("not a Chumby's name")
    try:
        if a.player is not None and not re.fullmatch(config.CHUMBY_NAME, a.player):
            ap.error("not an app's name")
        render(a.out, config.load(a.config), a.chumby, a.info, player=a.player)
    except WidgetError as e:
        print(f"render: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
