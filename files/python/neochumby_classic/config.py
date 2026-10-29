"""config: the settings file, and the registry of widgets that can go in it.

The web page (web.py) writes it; the renderer reads it at the start of every render.
Shaped like chumby.com's "my channels": a channel is an ordered line-up of apps. An app
(a widget, see widget.py and docs/widgets.md) is ONE card (Weather, Stock Quotes, ...)
that shows one or more sub-slides (weather: current / local / extended), each of which
the user can switch off or reorder in the app's "customize" dialog, plus its own options.

    {
      "version": 2,
      "channel": "default",                      # the channel a Chumby plays by default
      "channels": {
        "default": {
          "title": "Default",
          "apps": [                              # play order
            {"app": "weather", "enabled": true, "seconds": 12,
             "subslides": [{"id": "current", "enabled": true}, {"id": "local", "enabled": true},
                           {"id": "extended", "enabled": true}],
             "options": {}},
            {"app": "stocks", "enabled": true, "seconds": 12,
             "subslides": [{"id": "summary", "enabled": true}, {"id": "details", "enabled": true}],
             "options": {"tickers": ["BHP.AX", "TSLA"], ...}},
            {"app": "aurora", "enabled": true, "seconds": 24, "subslides": [], "options": {}}
          ]
        }
      },
      "chumbys": {"kitchen": "default"},         # a Chumby's channel, if not "channel"
      "general": {"config_url": "http://neochumbyd.local:8080/", "timezone": "Australia/Adelaide",
                  "latitude": null, "longitude": null}  # null: the weather station's
    }

Only the "default" channel exists for now; more channels later is a data change.
"remove" on the web page sets enabled=false (options are kept); "add apps" sets it back.

Option fields (a widget's CONFIG_SCHEMA, and GENERAL_SCHEMA below):
{"key", "type", "label", "default", "help"} with type list (of strings: "pattern",
"max_items", "upper"), string ("pattern", "max_len"), bool, choice ("choices": [[value,
label], ...]), number ("min", "max", "integer") or image_library (below). A string or
number field with "optional": True may also be left blank ("" or None). A string field
with "suggest": True gets a list to pick from on the web page: the module's
choices(key, options, fetch) hook. The web page builds forms from these, and validate()
is the only gate between a form (or a hand-edited file) and the renderer. A module's
optional migrate(options) -> options hook turns an older version's stored options into
the current ones before they are validated.

IMAGE LIBRARIES (the interface the Photos widget builds on; keep it stable):
    field   {"key": "photos", "type": "image_library", "label": "Photos", "default": [],
             "max_items": 200, "max_file_mb": 25, "max_total_mb": 500, "help": "..."}
    stored  options[key] = ordered list of image ids (str, [A-Za-z0-9_-]{1,64}); nothing else.
    hooks the module provides:
      ingest_image(src_path) -> id   called by the web page once per upload, with a temp file
                                     that Pillow has already decoded successfully (any format
                                     Pillow opens, HEIC too if pillow-heif is installed). The
                                     module does all the processing (EXIF orientation, strip
                                     EXIF/GPS, downscale, dithering), stores the result under
                                     storage_dir() and returns a fresh random id. The temp file
                                     is deleted afterwards, so copy what you need. Raise
                                     ValueError("why") to reject an image.
      storage_dir() -> path          where its files live; the page only serves files inside it
      thumb_path(id) -> path         a JPEG/PNG/WebP preview (~160-640 px) for the page's grid
      delete_image(id)               optional: called after a save drops the id from the library
    The page enforces max_file_mb per file, max_total_mb on storage_dir()'s total size,
    max_items, a real Pillow decode (never the extension or client type), and never uses the
    client's file name anywhere. Ids in a saved form are only accepted if they were already
    in the library, so a form can reorder or drop images but never point at arbitrary files.

The engine plays at most MAXP slideshow pages (limits.py). plan() expands enabled apps x enabled
sub-slides in channel order and drops whatever doesn't fit, so the renderer truncates
predictably and the web page can say exactly what was dropped.

Path: --config PATH, else $NEOCHUMBY_CLASSIC_CONFIG, else config.json in
$NEOCHUMBY_CLASSIC_STATE, else DEFAULT_PATH. A missing or broken file means defaults,
never a failed render.
"""
import copy
import json
import os
import re
import sys
import tempfile

from . import limits, output, widget

DEFAULT_STATE = "/var/lib/neochumby-classic"
DEFAULT_PATH = os.path.join(DEFAULT_STATE, "config.json")
# Sent with every fetch. Some sources refuse requests without a browser-like agent.
USER_AGENT = "Mozilla/5.0 (compatible; neochumby-classic; +https://github.com/phoozle/neochumby-classic)"
DEFAULT_TZ = "Australia/Adelaide"
MAXP = limits.MAXP             # the engine's slideshow page limit (limits.py)

# in the registry but switched off in a fresh config: an empty photo library has nothing
# to show, and its pages would crowd the slideshow; Whyalla News is one town's paper (an
# optional widget: owners add it from "add apps"); Pajama Sam needs the owner's own game files.
DEFAULT_OFF = {"photos", "whyallanews", "pajamasam"}
ORDER = ["weather", "news", "whyallanews", "stocks", "photos", "aurora", "pajamasam"]   # default play order of whatever exists
IMAGE_ID = r"[A-Za-z0-9_-]{1,64}"
CHUMBY_NAME = r"[a-z0-9_-]{1,32}"                           # neochumbyd's names for Chumbys
CHANNEL_ID = r"[a-z0-9_-]{1,24}"

# Night mode needs a place for the sun: these, if set, else the Weather widget's station.
OLD_DEFAULT_LATLON = (-33.0328, 137.5648)   # what earlier versions saved for everyone: treated as unset
GENERAL_SCHEMA = [
    {"key": "config_url", "type": "string", "label": "Address in the Settings QR code",
     "default": "http://neochumbyd.local:8080/", "max_len": 120,
     "pattern": r"https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~\-/]*)?",
     "help": "Where phones go when they scan the code on the Chumby: this page's address."},
    {"key": "timezone", "type": "string", "label": "Time zone", "default": DEFAULT_TZ, "max_len": 40,
     "pattern": r"[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}", "help": "For the clock and the times on pages (Australia/Sydney)."},
    {"key": "latitude", "type": "number", "label": "Latitude", "default": None, "min": -90, "max": 90, "optional": True,
     "help": "For sunrise and sunset, which switch night mode on and off. Blank: the Weather app's station's."},
    {"key": "longitude", "type": "number", "label": "Longitude", "default": None, "min": -180, "max": 180,
     "optional": True},
]
SECONDS = (5, 120)             # allowed seconds per slide


def state_root():
    """This program's state directory: the config, photos, caches and refresh requests."""
    return os.environ.get("NEOCHUMBY_CLASSIC_STATE") or DEFAULT_STATE


def default_path():
    if os.environ.get("NEOCHUMBY_CLASSIC_CONFIG"):
        return os.environ["NEOCHUMBY_CLASSIC_CONFIG"]
    return os.path.join(state_root(), "config.json")


def state_dir(path=None):
    """Where caches live (next to the config file)."""
    return os.path.dirname(os.path.abspath(path or default_path()))


# ---------------------------------------------------------------- registry
_reg = None


def registry():
    """[app dicts] with id, name, icon, description, seconds, subslides, schema, module and
    icon_path: every installed widget (widget.discover), in ORDER, then the others by id."""
    global _reg
    if _reg is not None:
        return _reg
    apps = []
    for name, m in widget.discover().items():
        a = dict(m.APP)
        a.setdefault("subslides", [])
        a.setdefault("seconds", 12)
        a.setdefault("description", "")
        a.setdefault("name", name)
        a["schema"] = getattr(m, "CONFIG_SCHEMA", [])
        a["module"] = m
        a["icon_path"] = widget.icon_path(m)
        apps.append(a)
    rank = {aid: i for i, aid in enumerate(ORDER)}
    _reg = sorted(apps, key=lambda a: (rank.get(a["id"], len(ORDER)), a["id"]))
    return _reg


def app_info(aid):
    return next((a for a in registry() if a["id"] == aid), None)


def defaults(schema):
    return {f["key"]: copy.deepcopy(f["default"]) for f in schema}


def default_entry(a, enabled=True):
    return {"app": a["id"], "enabled": enabled, "seconds": a["seconds"],
            "subslides": [{"id": s["id"], "enabled": bool(s.get("default", True))} for s in a["subslides"]],
            "options": defaults(a["schema"])}


# ---------------------------------------------------------------- validation
def validate(schema, raw):
    """Coerce raw values (JSON types, or form strings) to the schema.
    Returns (clean, errors): clean has every key (defaults where missing or bad),
    errors maps key -> message for values that were present but wrong."""
    clean, errors = defaults(schema), {}
    raw = raw if isinstance(raw, dict) else {}
    for fld in schema:
        k, t = fld["key"], fld["type"]
        if k not in raw:
            continue
        v = raw[k]
        if fld.get("optional") and t in ("string", "number") and (v is None or str(v).strip() == ""):
            clean[k] = "" if t == "string" else None
            continue
        try:
            if t == "bool":
                clean[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
            elif t == "number":
                n = float(str(v).strip())
                if n != n or n in (float("inf"), float("-inf")):
                    raise ValueError("not a number")
                if fld.get("integer"):
                    n = int(round(n))
                lo, hi = fld.get("min"), fld.get("max")
                if (lo is not None and n < lo) or (hi is not None and n > hi):
                    raise ValueError(f"must be between {lo} and {hi}")
                clean[k] = n
            elif t == "choice":
                if v not in [c[0] for c in fld["choices"]]:
                    raise ValueError("not one of the choices")
                clean[k] = v
            elif t == "string":
                s = str(v).strip()
                if len(s) > fld.get("max_len", 200):
                    raise ValueError("too long")
                if fld.get("pattern") and not re.fullmatch(fld["pattern"], s):
                    raise ValueError("doesn't look right")
                clean[k] = s
            elif t == "image_library":
                if not isinstance(v, list):
                    raise ValueError("not a list of images")
                out = []
                for it in v:
                    if isinstance(it, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", it) and it not in out:
                        out.append(it)
                clean[k] = out[:fld.get("max_items", 200)]
            elif t == "list":
                items = v if isinstance(v, list) else re.split(r"[\s,]+", str(v))
                out, bad = [], []
                for it in items:
                    s = str(it).strip()
                    if not s:
                        continue
                    if fld.get("upper"):
                        s = s.upper()
                    if len(s) > 32 or (fld.get("pattern") and not re.fullmatch(fld["pattern"], s)):
                        bad.append(s[:32])
                    elif s not in out:
                        out.append(s)
                if bad:
                    raise ValueError("not valid: " + ", ".join(bad))
                if len(out) > fld.get("max_items", 50):
                    raise ValueError(f"at most {fld.get('max_items', 50)}")
                clean[k] = out
        except (ValueError, TypeError) as e:
            msg = str(e)
            errors[k] = msg if msg and not msg.startswith("could not convert") else "not a valid value"
    return clean, errors


def validate_seconds(v, dflt):
    try:
        n = int(round(float(str(v))))
    except (ValueError, TypeError, OverflowError):
        return dflt, "not a number"
    if not SECONDS[0] <= n <= SECONDS[1]:
        return dflt, f"must be between {SECONDS[0]} and {SECONDS[1]}"
    return n, None


def normalise_subslides(a, raw):
    """Keep the stored order and on/off of known sub-slides; append new ones with their defaults."""
    known = {s["id"]: s for s in a["subslides"]}
    out, seen = [], set()
    for s in raw if isinstance(raw, list) else []:
        if isinstance(s, dict) and s.get("id") in known and s["id"] not in seen:
            seen.add(s["id"])
            out.append({"id": s["id"], "enabled": s.get("enabled", True) is not False})
    for sid, s in known.items():
        if sid not in seen:
            out.append({"id": sid, "enabled": bool(s.get("default", True))})
    return out


# ---------------------------------------------------------------- load / save
def default_config():
    return {"version": 2, "channel": "default",
            "channels": {"default": {"title": "Default", "apps": [
                default_entry(a, enabled=a["id"] not in DEFAULT_OFF) for a in registry()]}},
            "chumbys": {}, "general": defaults(GENERAL_SCHEMA)}


def normalise(cfg):
    """Any dict -> a complete, valid config. Unknown apps and keys are dropped, and every
    known app appears exactly once per channel (missing ones appended, removed)."""
    reg = {a["id"]: a for a in registry()}
    if not isinstance(cfg, dict):
        return default_config()
    out = {"version": 2, "channels": {}}
    chans = cfg.get("channels") if isinstance(cfg.get("channels"), dict) else {}
    for cid, ch in chans.items():
        if not (isinstance(cid, str) and re.fullmatch(CHANNEL_ID, cid) and isinstance(ch, dict)):
            continue
        apps, seen = [], set()
        for e in ch.get("apps") if isinstance(ch.get("apps"), list) else []:
            if not isinstance(e, dict) or e.get("app") not in reg or e["app"] in seen:
                continue
            a = reg[e["app"]]
            seen.add(a["id"])
            opts, _ = validate(a["schema"], migrate(a, e.get("options")))
            secs, _ = validate_seconds(e.get("seconds", a["seconds"]), a["seconds"])
            apps.append({"app": a["id"], "enabled": e.get("enabled", True) is not False, "seconds": secs,
                         "subslides": normalise_subslides(a, e.get("subslides")), "options": opts})
        for aid, a in reg.items():
            if aid not in seen:
                apps.append(default_entry(a, enabled=False))
        out["channels"][cid] = {"title": str(ch.get("title") or cid.title())[:40], "apps": apps}
    if not out["channels"]:
        out["channels"] = default_config()["channels"]
    out["channel"] = cfg.get("channel") if cfg.get("channel") in out["channels"] else next(iter(out["channels"]))
    general = dict(cfg.get("general")) if isinstance(cfg.get("general"), dict) else {}
    for chan in chans.values():                         # older files kept the place in weather's options
        for e in chan.get("apps", []) if isinstance(chan, dict) and isinstance(chan.get("apps"), list) else []:
            if isinstance(e, dict) and e.get("app") == "weather" and isinstance(e.get("options"), dict):
                for k in ("latitude", "longitude"):
                    if k in e["options"]:
                        general.setdefault(k, e["options"][k])
    if (general.get("latitude"), general.get("longitude")) == OLD_DEFAULT_LATLON:
        general["latitude"] = general["longitude"] = None
    out["general"], _ = validate(GENERAL_SCHEMA, general)
    raw = cfg.get("chumbys") if isinstance(cfg.get("chumbys"), dict) else {}
    out["chumbys"] = {n: c for n, c in raw.items()
                      if isinstance(n, str) and re.fullmatch(CHUMBY_NAME, n) and c in out["channels"]}
    return out


def migrate(a, options):
    """A widget's stored options, through its migrate() hook if it has one."""
    fn = getattr(a.get("module"), "migrate", None)
    if fn and isinstance(options, dict):
        try:
            return fn(dict(options))
        except Exception as e:
            print(f"config: {a['id']}: migrate failed: {e}", file=sys.stderr)
    return options


def load(path=None):
    path = path or default_path()
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return default_config()
    except (OSError, ValueError) as e:
        print(f"config: {path} unreadable ({e}); using defaults", file=sys.stderr)
        return default_config()
    return normalise(raw)


def save(cfg, path=None):
    """Atomic: a temp file beside it, then rename, so the renderer never reads half a file."""
    path = path or default_path()
    cfg = normalise(cfg)
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config.", dir=d)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return cfg


# ---------------------------------------------------------------- for the renderer
def channel(cfg, cid=None):
    return cfg["channels"].get(cid or cfg["channel"]) or next(iter(cfg["channels"].values()))


def channel_of(cfg, chumby):
    """The id of the channel `chumby` plays: its own, else the default one."""
    return cfg.get("chumbys", {}).get(chumby) or cfg["channel"]


def app_options(cfg, aid, cid=None):
    """An app's options in the channel (its defaults if it isn't there)."""
    e = next((e for e in channel(cfg, cid)["apps"] if e["app"] == aid), None)
    if e:
        return e["options"]
    a = app_info(aid)
    return defaults(a["schema"]) if a else {}


def page_count(a, sid, options):
    fn = getattr(a.get("module"), "page_count", None)
    try:
        return max(0, int(fn(sid, options))) if fn else 1
    except Exception:
        return 1


def plan(cfg, cid=None, limit=MAXP):
    """What the Chumby will play: (kept, dropped), each a list of slide dicts
        {"app", "subslide" (None for an app without sub-slides), "pages", "seconds", "options"}
    in channel order. A slide that doesn't fit whole in the remaining pages is cut to what
    fits (stocks: fewer detail pages); everything after the limit is dropped. With nothing
    enabled at all it falls back to weather's first sub-slide (the engine needs a page)."""
    reg = {a["id"]: a for a in registry()}
    kept, dropped, used = [], [], 0
    for e in channel(cfg, cid)["apps"]:
        a = reg.get(e["app"])
        if not a or not e["enabled"]:
            continue
        subs = [s["id"] for s in e["subslides"] if s["enabled"]] if a["subslides"] else [None]
        for sid in subs:
            n = page_count(a, sid, e["options"])          # sid None: an app without sub-slides
            if n <= 0:
                continue
            slide = {"app": a["id"], "subslide": sid, "pages": n, "seconds": e["seconds"], "options": e["options"]}
            room = limit - used
            if room <= 0:
                dropped.append(slide)
            elif n > room:
                kept.append(dict(slide, pages=room))
                dropped.append(dict(slide, pages=n - room))
                used = limit
            else:
                kept.append(slide)
                used += n
    if not kept and "weather" in reg:                     # the engine needs a page: fall back to the weather
        kept = [{"app": "weather", "subslide": "current", "pages": 1, "seconds": 12,
                 "options": app_options(cfg, "weather", cid)}]
    return kept, dropped


def write_if_changed(rgb565, img, path):
    """Write img as RGB565 to path only if the bytes differ from what is there, so unchanged
    pages keep their mtime and delivery skips them. Returns True if it wrote. (The rgb565
    argument is kept for the widgets that pass one; output.rgb565 does the work.)"""
    return output.rgb565(img, path)


def write_render_index(out, index):
    """apps.json in the render dir: {app_id: [page .raw files]}, for the web page's
    previews in the customize dialog. Written only when it changes."""
    try:
        output.write_if_changed(os.path.join(out, "apps.json"), json.dumps(index, sort_keys=True).encode())
    except OSError as e:
        print(f"config: can't write apps.json: {e}", file=sys.stderr)
