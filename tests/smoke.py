#!/usr/bin/env python3
"""smoke: render everything offline, with made-up data, and check the result.

  python3 tests/smoke.py [--out DIR] [--screenshots DIR]      (CI runs it)

Every widget is switched on (the photo library gets two made-up pictures), the network is
fakenet's, the clock is frozen and no owner pictures are supplied (the placeholders, as
published), so the run is the same everywhere. It checks that:
  - every built-in widget is found through its entry point (or, uninstalled, through
    widget.BUILTIN, which must match pyproject.toml);
  - the output directory follows docs/interface.md: the manifest names only files that
    exist, with the sizes their directives say, hashes.txt covers them, version matches;
  - every widget put its pages in the slideshow, and every screen a tap can open exists;
  - Whyalla News keeps to the paywall: a subscriber-only story shows only its excerpt and
    a "Continue at" line, a full-text story its text, never a form, script or off-site link;
  - Settings -> Network and Chumby Info show "?" for every fact the Chumby hasn't reported;
  - the weather: with no place chosen, a page saying where to choose it (and nothing
    fetched); a place in another state (NSW) and the ACT; the forecast town matched by name
    or chosen; the sun times from the station; old options migrated; renders that change
    nothing write nothing;
  - limits.py and docs/widgets.md's table of the engine's limits agree;
  - Flurry: its page and the engine's `flurry` line, with the options it was given, and
    that lines the engine would skip are refused;
  - Pajama Sam (synthetic clips only, from a stand-in converter: no game data anywhere):
    the "add your files" page, the folder checks, Convert on the web page with its
    progress and exit codes, the clip page, and that a second render writes nothing;
  - Home -> APPS: a page of tiles per six apps in a made-up neochumbyd tree, each opening
    its app ("app NAME"), paged, without classic, home or apps that can't be opened;
  - the engine's limits and the per-page memory budget hold;
  - a second render with the same data writes nothing at all (no file, no mtime);
  - a change re-renders only what it touches, and the version goes up;
  - the renderer service schedules new Chumbys, refresh requests and per-Chumby requests;
  - the web page: its pages, Host and origin checks, sign-in with the owner token, CSRF,
    and its client of neochumbyd's admin socket (against a stand-in speaking #9's
    protocol), including that only start=true apps are offered as start apps.
--screenshots writes PNGs of a few pages and screens, for the README.
"""
import argparse
import datetime as dt
import http.client
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
import time
import tomllib
import urllib.parse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
try:
    import neochumby_classic  # noqa: F401
except ImportError:                                          # not installed: run from the source tree
    sys.path.insert(0, os.path.join(ROOT, "files", "python"))

import fakenet  # noqa: E402

NOW = dt.datetime(2026, 10, 28, 19, 42, 17, tzinfo=ZoneInfo("Australia/Adelaide"))
fakenet.install(NOW)
OWNER = "0123456789abcdef0123456789abcdef"
FAILS = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        FAILS.append(what)


def manifest(out):
    with open(os.path.join(out, "manifest.txt")) as fh:
        return [ln.split() for ln in fh.read().splitlines() if ln and not ln.startswith("#")]


def sizes_ok(out, lines):
    """Each picture's size from its directive: (file, expected bytes) pairs."""
    want = []
    for t in lines:
        if t[0] in ("page", "pagenight", "screen"):              # page N FILE ..., screen NAME FILE
            want.append((t[2], 320 * 240 * 2))
        elif t[0] == "pane":                                    # pane SCREEN FILE W H X Y VAR N
            want.append((t[2], int(t[3]) * int(t[4]) * int(t[8]) * 2))
        elif t[0] == "sprite":                                  # sprite PAGE FILE W H FRAMES X Y FPS
            want.append((t[2], int(t[3]) * int(t[4]) * int(t[5]) * 2))
        elif t[0] in ("glow", "bgscroll"):                      # glow PAGE FILE W H ..., bgscroll PAGE FILE W
            want.append((t[2], int(t[3]) * (int(t[4]) if t[0] == "glow" else 240) * 2))
    bad = [(f, n, os.path.getsize(os.path.join(out, f)) if os.path.exists(os.path.join(out, f)) else None)
           for f, n in want if not os.path.exists(os.path.join(out, f)) or os.path.getsize(os.path.join(out, f)) != n]
    return want, bad


def snapshot(out):
    """Every file in the directory, with its mtime and size."""
    return {f: (os.stat(os.path.join(out, f)).st_mtime_ns, os.path.getsize(os.path.join(out, f))) for f in os.listdir(out)}


# ---------------------------------------------------------------- widgets
def widgets_found():
    from neochumby_classic import config, widget
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        declared = tomllib.load(fh)["project"]["entry-points"][widget.GROUP]
    check(declared == widget.BUILTIN, "widget.BUILTIN matches pyproject.toml's entry points")
    ids = [a["id"] for a in config.registry()]
    check(ids == ["weather", "news", "whyallanews", "stocks", "photos", "aurora", "flurry", "pajamasam"],
          f"the eight built-in widgets are found: {ids}")
    for a in config.registry():
        check(a["icon_path"] is not None, f"{a['id']} has an icon")


# ---------------------------------------------------------------- rendering
def renders(work, out, shots):
    from neochumby_classic import config, render
    from neochumby_classic.widgets import photos

    ids = []
    for k in range(2):
        src = os.path.join(work, f"in{k}.jpg")
        with open(src, "wb") as fh:
            fh.write(fakenet.picture(f"photo{k}", 1200, 900))
        ids.append(photos.ingest_image(src))
    cfg = config.default_config()
    for e in config.channel(cfg)["apps"]:
        e["enabled"] = True
        if e["app"] == "photos":
            e["options"]["library"] = ids
        if e["app"] == "weather":                         # the tests' example place
            e["options"].update(state="SA", station="95664")
    config.save(cfg)
    kept, dropped = config.plan(config.load())
    check(not dropped, f"plan fits the page limit ({sum(s['pages'] for s in kept)} pages)")

    changed = render.render(out, config.load(), "kitchen", now=NOW)
    check(changed, "the first render writes")
    lines = manifest(out)
    pages = [t for t in lines if t[0] == "page"]
    check(len(pages) == sum(s["pages"] for s in kept), f"{len(pages)} slideshow pages, as planned")
    for app, prefix in (("weather", "weather_"), ("news", "news_slide_"), ("whyallanews", "wn_slide"), ("stocks", "stocks_"),
                        ("photos", "photo"), ("aurora", "aurora_"), ("flurry", "flurry_"), ("pajamasam", "pjs_setup")):
        check(any(t[2].startswith(prefix) for t in pages), f"{app} has a page")
    screens = {t[1] for t in lines if t[0] == "screen"}
    check("music" not in {t[1] for t in lines if t[0] == "screen"} and not any(t[0] == "station" for t in lines),
          "no Music screen or stations: music is an app of its own, a player")
    hot = {(t[1], " ".join(t[6:])) for t in lines if t[0] == "hot"}
    check(("home", "player apps_p0") in hot and ("home", "player_toggle") in hot,
          "Home: MUSIC opens the player (else APPS), MUTE toggles it")
    check(any(t[0] == "pane" and t[1] == "home" and t[7] == "player" for t in lines), "Home: MUTE's face follows the player")
    for want in ("home", "home_night", "settings", "timing", "network", "about", "configure", "confirm",
                 "stocks_list", "news_nat_l0", "wn_l0", "wn_l0_night", "wn_s0_p0", "wn_s0_p0_night"):
        check(want in screens, f"screen {want}")
    targets = {t[-1] for t in lines if t[0] == "hot" and t[-2] == "screen"} | {t[2] for t in lines if t[0] == "pagetap"}
    check(targets <= screens, f"every screen a tap opens exists ({len(targets)})")
    want, bad = sizes_ok(out, lines)
    check(not bad, f"{len(want)} pictures have the sizes their directives say {bad[:3]}")
    named = set(render.referenced_files(out, [" ".join(t) for t in lines]))
    missing = [f for t in lines for f in t if f.endswith((".raw", ".l8")) and f not in named]
    check(not missing, f"all {len(named)} files the manifest names exist {missing[:3]}")
    with open(os.path.join(out, "hashes.txt")) as fh:
        hashed = dict(reversed(ln.split()) for ln in fh if ln.strip())
    check(set(hashed) == named, "hashes.txt lists exactly the named files")
    check(all(render.md5_file(os.path.join(out, f)) == h for f, h in hashed.items()), "hashes.txt is right")
    with open(os.path.join(out, "manifest.txt")) as fh:
        head = fh.read(200).splitlines()
    version, digest = render.read_version(out)
    check(head[0] == f"# classic {digest}" and head[1] == f"version {version}", "version matches the manifest")
    extra = [f for f in os.listdir(out) if f not in named and f not in ("manifest.txt", "hashes.txt", "version", "apps.json")]
    check(not extra, f"nothing else in the directory {extra[:5]}")
    check(not any(f.startswith(".") for f in os.listdir(out)), "no temp files left")
    joined = [" ".join(t) for t in lines]
    try:
        render.check_limits(joined)
        check(True, "within the engine's limits")
    except render.WidgetError as e:
        check(False, str(e))
    use = render.memory_use(out, joined)
    worst = max(use.items(), key=lambda kv: kv[1])
    check(worst[1] <= render.PAGE_BUDGET, f"per-page memory: the most is {worst[0][0]} {worst[0][1]}, {worst[1] // 1024} KB")
    for kind, n in (("page", config.MAXP),):
        check(sum(1 for t in lines if t[0] == kind) <= n, f"{kind}s within {n}")

    before = snapshot(out)
    time.sleep(0.05)
    changed = render.render(out, config.load(), "kitchen", now=NOW)
    after = snapshot(out)
    diff = sorted(f for f in set(before) | set(after) if before.get(f) != after.get(f))
    check(not changed and not diff, f"a second render with the same data writes nothing {diff[:5]}")

    cfg = config.load()
    for e in config.channel(cfg)["apps"]:
        if e["app"] == "stocks":
            e["options"]["tickers"] = e["options"]["tickers"][:3]
    config.save(cfg)
    render.render(out, config.load(), "kitchen", now=NOW)
    after2 = snapshot(out)
    diff = sorted(f for f in after2 if after.get(f) != after2[f])
    v2, _ = render.read_version(out)
    check(v2 > version and "manifest.txt" in diff and "version" in diff, f"a change raises the version ({version} -> {v2})")
    check(len(diff) < len(after2) // 2, f"a change rewrites only what it touches ({len(diff)} of {len(after2)} files)")

    if shots:
        from neochumby_classic import preview
        os.makedirs(shots, exist_ok=True)
        lines = manifest(out)
        first = {}
        for t in lines:
            if t[0] == "pageapp":
                first.setdefault(t[2], int(t[1]))
        pages = {t[2].removesuffix(".raw"): int(t[1]) for t in lines if t[0] == "page"}
        for name, img in (("weather", preview.page(out, pages["weather_current"])),
                          ("forecast", preview.page(out, pages["weather_local"])),
                          ("weather-night", preview.page(out, pages["weather_current"], night=True)),
                          ("news", preview.page(out, pages["news_slide_nat"])),
                          ("stocks", preview.page(out, pages["stocks_summary"])),
                          ("photos", preview.page(out, pages["photos_0"])),
                          ("aurora", preview.page(out, pages["aurora_bg"])),
                          ("flurry", preview.page(out, pages["flurry_still"])),
                          ("home", preview.screen(out, "home", values=preview.EXAMPLE)),
                          ("settings", preview.screen(out, "settings"))):
            img.resize((640, 480), resample=0).save(os.path.join(shots, f"{name}.png"), optimize=True)
        print(f"      screenshots in {shots}")


# ---------------------------------------------------------------- Whyalla News and the paywall
def whyalla(work, out):
    import datetime as dt2
    from neochumby_classic.widgets import whyallanews as wn
    asked = [u for u in fakenet.REQUESTS if "whyallanewsonline" in u]
    check(asked and all(u == wn.FEED or "/wp-content/uploads/" in u for u in asked),
          f"whyallanews: fetches only the feed and its photos ({len(asked)} requests, all canned)")
    items = wn.parse_feed(fakenet.wordpress_feed(NOW), 12)
    check(len(items) == 9 and all(it["link"].startswith(f"https://{wn.SITE}/") for it in items),
          "whyallanews: a link off the site is skipped")
    walled = [it for it in items if it["paywalled"]]
    full = [it for it in items if not it["paywalled"]]
    check(walled and full and all(it["cat"] != "In Business" for it in walled) and all(it["cat"] == "In Business" for it in full),
          f"whyallanews: {len(walled)} subscriber-only stories, {len(full)} in full")
    w = walled[0]
    text = " ".join(t for _, t in w["paras"])
    check(len(w["paras"]) == 1 and text.endswith("…") and not any(x in text for x in ("Subscribe", "Login", "Username", "Log In")),
          f"whyallanews: a paywalled story keeps only its excerpt: {text!r}")
    f = full[0]
    text = " ".join(t for _, t in f["paras"])
    check(len(f["paras"]) == 8 and ("h", "What happens next") in f["paras"]
          and "alert" not in text and "appeared first on" not in text,
          "whyallanews: a full story keeps its text and heading, without the script or the 'appeared first on' line")
    check(w["photo"] is None and w["img"].startswith(f"https://{wn.SITE}/wp-content/uploads/"), "whyallanews: photo links parsed")
    pages = wn.layout_story(w, NOW, wn.MAX_STORY_PAGES)
    check(len(pages) == 1 and pages[-1][-1][0] == "more" and pages[-1][-1][3] == "Continue at " + wn.SITE,
          "whyallanews: a paywalled story is one page ending 'Continue at whyallanewsonline.com.au'")
    pages = wn.layout_story(f, NOW, wn.MAX_STORY_PAGES)
    check(1 < len(pages) <= wn.MAX_STORY_PAGES and all(op[0] != "more" or "The rest at" in op[3] for p in pages for op in p),
          f"whyallanews: a full story runs to {len(pages)} pages, with no subscriber line")
    lines = manifest(out)
    story = {t[1] for t in lines if t[0] == "screen" and t[1].startswith("wn_s")}
    check(len(story) >= 2 * len(items[:8]), f"whyallanews: {len(story)} story screens, light and night")
    check(not any(f.startswith(".wncache") for f in os.listdir(out)), "whyallanews: caches stay out of the render")
    check(dt2.datetime.now(NOW.tzinfo) == NOW, "the clock is still frozen")


# ---------------------------------------------------------------- the weather, anywhere in Australia
def weather_places(work):
    from neochumby_classic import config, render, limits
    from neochumby_classic.widgets import weather as wx

    def channel(options=None, general=None):
        cfg = config.default_config()
        for e in config.channel(cfg)["apps"]:
            e["enabled"] = e["app"] == "weather"
            if e["app"] == "weather" and options is not None:
                e["options"] = options
        cfg["general"].update(general or {})
        return config.normalise(cfg)

    def run(name, cfg):
        out = os.path.join(work, "wx", name)
        n = len(fakenet.REQUESTS)
        render.render(out, cfg, "kitchen", now=NOW)
        return out, manifest(out), fakenet.REQUESTS[n:]

    fresh = config.default_config()
    check(config.app_options(fresh, "weather")["state"] == "" and fresh["general"]["latitude"] is None,
          "weather: a fresh config has no place, and no latitude")
    check(not next(e for e in config.channel(fresh)["apps"] if e["app"] == "whyallanews")["enabled"],
          "whyallanews: off in a fresh channel (owners add it)")
    out, lines, asked = run("unset", channel())
    pages = [t for t in lines if t[0] == "page"]
    check([t[2] for t in pages] == ["weather_setup.raw"] and not any(t[0] == "sun" for t in lines)
          and not any("bom.gov.au" in u for u in asked),
          "weather: no place yet: one 'choose your location' page, nothing fetched, no sun times")
    check(any(t[0] == "ticker" for t in lines) and any(t == ["pagenight", "0", "weather_setup_night.raw"] for t in lines),
          "weather: ...with its night copy, the clock and the ticker")

    sydney = {"state": "NSW", "station": "94768"}
    out, lines, asked = run("nsw", channel(sydney))
    pages = [t[2] for t in lines if t[0] == "page"]
    check(pages == ["weather_current.raw", "weather_local.raw", "weather_extended.raw"]
          and sorted(u.rsplit("/", 1)[1] for u in asked if "bom.gov.au" in u) == ["IDN11060.xml", "IDN60920.xml"],
          "weather: New South Wales from its own products")
    check(wx.LOC["name"] == "Sydney", f"weather: the station's name as the place: {wx.LOC['name']!r}")
    sun = [" ".join(t) for t in lines if t[0] == "sun"]
    check(sun == render.sun_lines(NOW, -33.8593, 151.2048), "weather: the sun times are the station's")
    obs, fc, st = wx.place(sydney, os.path.join(work, "wxcache"))
    want = wx.get_forecast(fakenet.bom_forecast(NOW, "IDN11060"), "NSW_PT131")
    check(fc == want and fc, "weather: the forecast town matched by name (Sydney - Observatory Hill -> Sydney)")
    before = snapshot(out)
    time.sleep(0.05)
    changed = render.render(out, channel(sydney), "kitchen", now=NOW)
    diff = sorted(f for f, v in snapshot(out).items() if before.get(f) != v)
    check(not changed and not diff, f"weather: a second render of another state writes nothing {diff[:5]}")
    _, lines, _ = run("own-latlon", channel(sydney, {"latitude": -12.5, "longitude": 130.8}))
    check([" ".join(t) for t in lines if t[0] == "sun"] == render.sun_lines(NOW, -12.5, 130.8),
          "weather: the owner's latitude and longitude win over the station's")

    _, fc, _ = wx.place({"state": "SA", "station": "94659"}, os.path.join(work, "wxcache"))
    check(fc == [], "weather: a station with no town of its name has no forecast")
    _, fc, _ = wx.place({"state": "SA", "station": "94659", "forecast_area": "SA_PT033"}, os.path.join(work, "wxcache"))
    check(fc and fc == wx.get_forecast(fakenet.bom_forecast(NOW, "IDS10044"), "SA_PT033"), "weather: ...until one is chosen")
    _, lines, _ = run("wrong", channel({"state": "SA", "station": "94768"}))
    check([t[2] for t in lines if t[0] == "page"] == ["weather_setup.raw"], "weather: a station from another state: choose again")

    stations = wx.choices("station", {"state": "ACT"}, fetch=True)
    check(stations == [["94926", "Canberra"]] and wx.choices("forecast_area", {"state": "ACT"}, fetch=True) == [["NSW_PT027", "Canberra"]],
          f"weather: the ACT lists only its own stations, and Canberra's forecast {stations}")
    _, lines, asked = run("act", channel({"state": "ACT", "station": "94926"}))
    check(len([t for t in lines if t[0] == "page"]) == 3 and "IDN10035.xml" in " ".join(asked), "weather: the ACT renders")
    check(wx.choices("station", {"state": ""}) == [] and wx.choices("name", {"state": "SA"}) == [], "weather: no list without a state")

    old = {"location": "Whyalla", "obs_product": "IDS60920", "obs_station": "95664", "forecast_product": "IDS10044",
           "forecast_area": "SA_PT055"}
    cfg = channel(old, {"latitude": -33.0328, "longitude": 137.5648})
    check(config.app_options(cfg, "weather") == {"state": "SA", "station": "95664", "forecast_area": "SA_PT055", "name": "Whyalla"}
          and cfg["general"]["latitude"] is None, "weather: options from before the state picker are migrated")

    k = wx.badge("Whyalla"), wx.badge(""), wx.badge("Port Augusta West Beach")
    check(all(b.size == (120, 96) for b in k) and k[0].tobytes() != k[1].tobytes(), "weather: the badge, same size, with the town")

    with open(os.path.join(ROOT, "docs", "widgets.md")) as fh:
        rows = re.findall(r"^\s*\| `([a-z]+)` \| [^|]+\| (\d+) \|", fh.read(), re.M)
    check(dict((k, int(v)) for k, v in rows) == limits.LIMITS, f"limits.py matches docs/widgets.md's table {rows}")


# ---------------------------------------------------------------- Flurry
def flurry(work, out):
    """Its page, the engine's line with the options given, and the lines the engine would skip."""
    from neochumby_classic import config, render
    from neochumby_classic.widgets import flurry as fl
    lines = manifest(out)
    page = next(t for t in lines if t[0] == "page" and t[2] == "flurry_still.raw")
    fline = [t for t in lines if t[0] == "flurry"]
    check(fline == [["flurry", page[1], "classic"]] and "noclock" in page and "noticker" in page,
          f"flurry: one page, drawn by the engine's classic Flurry {fline}")
    check(fl.line(3, {"preset": "fire", "streams": "6", "colour": "rainbow", "speed": "0.5"})
          == "flurry 3 fire streams=6 colour=rainbow speed=0.5"
          and fl.line(3, {"preset": "nonesuch", "streams": "preset", "colour": "plaid", "speed": "9"}) == "flurry 3 classic",
          "flurry: options to the line, and anything else left to the preset")
    schema = {f["key"]: f for f in fl.CONFIG_SCHEMA}
    clean, errs = config.validate(fl.CONFIG_SCHEMA, {"streams": "99", "speed": "2"})
    check("streams" in errs and clean["streams"] == "preset" and clean["speed"] == "2",
          "flurry: the form refuses streams=99 and keeps the rest")
    check(all(c[0] in render.FLURRY_PRESETS for c in schema["preset"]["choices"])
          and all(c[0] == "preset" or c[0] in render.FLURRY_COLOURS for c in schema["colour"]["choices"]),
          "flurry: the form offers only what the engine takes")
    good = ["page 0 a.raw 30", "flurry 0 water streams=3 colour=tiedye speed=1.5 later=1"]
    check(not render.problems(good), "flurry: a good line passes (an unknown key is a later engine's)")
    for bad in ("flurry 1 classic", "flurry 0 nonesuch", "flurry 0 streams=17", "flurry 0 colour=plaid",
                "flurry 0 speed=4.5", "flurry 0 classic rgb", "flurry 0 puffs=10"):
        check(render.problems(good[:1] + [bad]), f"flurry: {bad!r} is refused")


# ---------------------------------------------------------------- Settings -> Network and Chumby Info
def chumby_info(work, out):
    from neochumby_classic import config, preview, render, screens
    rows = screens.network_rows(render.chumby_info(None, {})) + screens.about_rows(render.chumby_info(None, {}))
    check(all(v == "?" or (v in (None, "") and k) for _, v, k in rows), "info: without the file, every row is live or '?'")
    path = os.path.join(work, "info-kitchen")
    with open(path, "w") as fh:
        fh.write("ssid=Home WiFi\nquality=60\nip=192.0.2.9\nuptime_s=99\nmemfree=1 2\nfirmware=1.0\nhostname=x\nusb=1 2\n"
                 "feed=spoofed\nbogus=1\nengine=\n")
    before = snapshot(out)
    time.sleep(0.05)
    changed = render.render(out, config.load(), "kitchen", info_path=path, now=NOW)
    diff = sorted(f for f, v in snapshot(out).items() if before.get(f) != v)
    check(not changed and not diff, f"info: what the Chumby reads itself is never drawn in: those keys change nothing {diff[:5]}")
    with open(path, "w") as fh:
        fh.write("signal=-61\nkernel=2.6.28-chumby\ngateway=192.0.2.1\nengine=neochumby 0.4\nmac=?x\nasof=19:30\n")
    info = render.chumby_info(path, {"feed": "weather of 28 Oct"})
    net = {label: v for label, v, _ in screens.network_rows(info)}
    about = {label: v for label, v, _ in screens.about_rows(info)}
    check(net["Signal"] == "-61 dBm" and net["Gateway"] == "192.0.2.1" and net["DNS"] == "?" and net["IP address"] is None,
          f"info: the keys the Chumby can't read show, missing ones '?' {net}")
    check(about["Kernel"] == "Linux 2.6.28-chumby" and about["Feed"] == "weather of 28 Oct" and "bogus" not in info,
          f"info: unknown keys are ignored {about}")
    render.render(out, config.load(), "kitchen", info_path=path, now=NOW)
    after = snapshot(out)
    diff = sorted(f for f, v in after.items() if before.get(f) != v and f.endswith(".raw"))
    check(diff and all(f.startswith(("network", "about")) for f in diff), f"info: those change only Network and Chumby Info {diff}")
    keys = re.findall(r"`([a-z_]+)`", open(os.path.join(ROOT, "docs", "interface.md")).read().split("## Facts from the Chumby")[1])
    check(set(render.INFO_KEYS) <= set(keys), f"info: docs/interface.md documents every key {sorted(set(render.INFO_KEYS) - set(keys))}")

    # live values (the engine's spec v1): the directives, and that they're valid
    lines = manifest(out)
    joined = [" ".join(t) for t in lines]
    check(render.problems(joined, out) == [], f"live: every line is one the engine takes {render.problems(joined, out)[:3]}")
    fonts = {t[1]: t for t in lines if t[0] == "livefont"}
    check(set(fonts) >= {"info", "info_night"} and all(len(t) == 5 + 79 for t in fonts.values()),
          "live: a glyph strip per theme, with 79 widths")
    live = {(t[1], t[2]) for t in lines if t[0] == "live"}
    for sfx in ("", "_night"):
        want = {("network" + sfx, k) for k in ("ssid", "wifi_pct", "ip")} | \
               {("about" + sfx, k) for k in ("name", "firmware", "uptime", "mem_free", "stick_free")}
        check(want <= live, f"live: Network{sfx} and Chumby Info{sfx} show the Chumby's own values {sorted(want - live)}")
        for scr in ("home", "network"):
            p = [t for t in lines if t[0] == "pane" and t[1] == scr + sfx and t[7] == "wifi"]
            check(len(p) == 1 and p[0][8] == "5", f"live: {scr}{sfx}'s signal bars are a 5-frame wifi pane")
    order = {}
    for k, t in enumerate(lines):
        order.setdefault((t[0], t[1]), k)
    check(all(order[("livefont", t[3])] < k and order[("screen", t[1])] < k for k, t in enumerate(lines) if t[0] == "live"),
          "live: each box comes after its font and its screen")
    bad = render.problems(["screen s s.raw", "livefont f s.raw 7 12 1 2", "live s nope f 0 0 10 10 left bg",
                           "live s ip g 0 0 10 10 left bg", "live s ip f 300 0 30 10 left bg", "live page:3 ip f 0 0 9 9 left bg",
                           "pane t x.raw 1 1 0 0 wifi 5", "live s ip f 0 0 10 10 middle #12345"])
    check(len(bad) == 7, f"live: lines the engine would skip are caught ({len(bad)} of 7)")
    plain = preview.screen(out, "network")
    shown = preview.screen(out, "network", values=preview.EXAMPLE)
    box = next(t for t in lines if t[:3] == ["live", "network", "ip"])
    x, y, w, h = map(int, box[4:8])
    check(plain.crop((x, y, x + w, y + h)).tobytes() != shown.crop((x, y, x + w, y + h)).tobytes(),
          "live: the preview draws the values in their boxes")


# ---------------------------------------------------------------- Home -> APPS
def apps_screen(work):
    from PIL import Image
    from neochumby_classic import appsscreen, config, render

    def fake_tree(n):
        root = os.path.join(work, f"tree{n}")
        for k in range(n):
            d = os.path.join(root, "apps", f"game{k:02d}")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "manifest"), "w") as fh:
                fh.write(f"title=GAME {k}\nexec=neochumby-game{k}\n" + ("colour=dc281e\n" if k % 2 else ""))
        for name, man in (("classic", "title=Classic\nexec=neochumby-classic\nstart=true\n"),
                          ("somafm", "title=SomaFM\nexec=neochumby-somafm\nplayer=true\n"),
                          ("radio", "title=Radio\nexec=neochumby-radio\nplayer=true\n"),
                          ("home", "title=Apps\nexec=neochumby-home\nstart=true\n"),
                          ("airplay", "title=AirPlay\nservice=neochumby-airplay\n"),        # no exec: can't be opened
                          ("Bad Name", "title=Bad\nexec=x\n")):
            os.makedirs(os.path.join(root, "apps", name), exist_ok=True)
            with open(os.path.join(root, "apps", name, "manifest"), "w") as fh:
                fh.write(man)
        Image.new("RGBA", (64, 64), (40, 200, 90, 255)).save(os.path.join(root, "apps", "game00", "icon.png"))
        return root

    cfg = config.default_config()
    for e in config.channel(cfg)["apps"]:
        e["enabled"] = False
    for n, pages in ((3, 1), (7, 2), (12, 3)):
        os.environ["NEOCHUMBY_TREE"] = fake_tree(n)
        apps = appsscreen.full_apps()
        check([a["name"] for a in apps[:2]] == ["radio", "somafm"] and all(a["player"] for a in apps[:2]),
              "apps: players first")
        apps = apps[2:]
        check(sorted(a["name"] for a in apps) == [f"game{k:02d}" for k in range(n)] and apps[0]["icon"],
              f"apps: {n} apps from the tree, without classic, home, one with no exec or a bad name")
        out = os.path.join(work, "apps", str(n))
        render.render(out, cfg, "kitchen", now=NOW)
        lines = manifest(out)
        screens = {t[1] for t in lines if t[0] == "screen"}
        want = {f"apps_p{p}{sfx}" for p in range(pages) for sfx in ("", "_night")}
        check(want <= screens and f"apps_p{pages}" not in screens, f"apps: {n} apps on {pages} page(s), with night copies")
        hots = [t for t in lines if t[0] == "hot" and t[1].startswith("apps_p")]
        opened = [t[7] for t in hots if t[6] == "app"]
        check(sorted(opened) == sorted([a["name"] for a in apps] * 2 + ["radio", "somafm"] * 2) and all(re.fullmatch(r"[a-z0-9_-]{1,32}", a) for a in opened),
              f"apps: every tile opens its app ('app NAME'), day and night ({len(opened)})")
        targets = {t[7] for t in hots if t[6] == "screen"}
        check(targets == {f"apps_p{p}" for p in range(pages)} | {"home"} if pages > 1 else targets == {"home"},
              f"apps: PREV / NEXT between pages, DONE to Home {sorted(targets)}")
        check(render.problems([" ".join(t) for t in lines], out) == [], "apps: every line is one the engine takes")
        before = snapshot(out)
        time.sleep(0.05)
        changed = render.render(out, cfg, "kitchen", now=NOW)
        diff = sorted(f for f, v in snapshot(out).items() if before.get(f) != v)
        check(not changed and not diff, f"apps: a second render writes nothing {diff[:5]}")
    home = [t for t in lines if t[0] == "hot" and t[1] == "home" and t[-2:] == ["screen", "apps_p0"]]
    check(len(home) == 1, "apps: Home's APPS opens them")
    from neochumby_classic import admin
    players = [("radio", "Radio"), ("somafm", "SomaFM")]
    check(admin.default_player(None, players) == "somafm" and admin.default_player("radio", players) == "radio"
          and admin.default_player("nope", players) == "somafm" and admin.default_player(None, players[:1]) == "radio"
          and admin.default_player("radio", []) is None,
          "apps: the starred player is the Chumby's, else SomaFM, else the first")
    plain = snapshot(out)
    render.render(out, cfg, "kitchen", now=NOW, player="radio")
    moved = sorted(f for f, v in snapshot(out).items() if plain.get(f) != v and f.endswith(".raw"))
    check(moved and all(f.startswith("apps_p0") for f in moved), f"apps: the star follows the Chumby's player {moved}")
    check(any(t[0] == "hot" and t[1] == "settings" and t[-2:] == ["screen", "configure"] for t in lines),
          "apps: Settings still leads to the web page's QR code")
    os.environ.pop("NEOCHUMBY_TREE")


# ---------------------------------------------------------------- Pajama Sam (synthetic clips only)
def pajamasam_web(work, b):
    """Convert on the web page, with the stand-in converter (tests/fake-pajamasam)."""
    from neochumby_classic import config
    from neochumby_classic.widgets import pajamasam as pjs
    os.environ["NEOCHUMBY_PAJAMASAM"] = os.path.join(HERE, "fake-pajamasam")
    games = os.path.join(work, "games")
    for d, marker in (("ps1", "Pajama.HE0"), ("empty", None)):     # empty marker files: no game data
        os.makedirs(os.path.join(games, d), exist_ok=True)
        if marker:
            open(os.path.join(games, d, marker), "w").close()
    for bad in ("games/ps1", "/etc", f"{games}/../games/ps1", os.path.join(games, "nope")):
        try:
            pjs.job_command({"ps1": bad})
            check(False, f"pajamasam: the folder {bad!r} is refused")
        except ValueError:
            check(True, f"pajamasam: the folder {bad!r} is refused")
    _, errs = config.validate(pjs.CONFIG_SCHEMA, {"ps1": "../x", "ps2": "/srv/games/$(id)"})
    check(set(errs) == {"ps1", "ps2"}, "pajamasam: a relative or odd folder isn't saved")

    def convert(folder):
        code, _, _ = b.go("POST", "/action", {"action": "save_app", "app": "pajamasam", "seconds": "30",
                                               "opt.ps1": folder, "opt.scenes": "2", "bool.setup_page": "1",
                                               "opt.setup_page": "1"})
        check(code == 303, f"pajamasam: the folder is saved ({code})")
        code, _, r = b.go("POST", "/action", {"action": "job", "app": "pajamasam"})
        check(code == 303 and "customize=pajamasam" in (r.getheader("Location") or ""), "web: Convert starts the job")
        for _ in range(100):
            _, body, _ = b.go("GET", "/api/job?app=pajamasam")
            st = json.loads(body)
            if not st["running"]:
                return st
            time.sleep(0.1)
        return st

    st = convert(os.path.join(games, "empty"))
    check(st["code"] == 2 and "No Pajama Sam game was recognised" in st["summary"] and any("missing" in ln for ln in st["lines"]),
          f"web: exit 2 is explained, with the progress lines {st['summary']!r}")
    st = convert(os.path.join(games, "ps1"))
    check(st["code"] == 0 and st["summary"] == pjs.EXIT[0] and st["lines"][-1].startswith("done clips=2"),
          f"web: Convert's progress and result {st['lines'][-1:]}")
    check(os.path.isfile(os.path.join(pjs.clip_dir(), "clips.txt")) and pjs.clip_dir().startswith(work),
          "pajamasam: the clips go only to the private clip directory")
    _, body, _ = b.go("GET", "/?customize=pajamasam")
    check('data-job="pajamasam"' in body and "Convert" in body, "web: the customize dialog has Convert and its progress")
    os.environ.pop("NEOCHUMBY_PAJAMASAM")


def pajamasam_render(work):
    from neochumby_classic import config, render
    from neochumby_classic.widgets import pajamasam as pjs

    def channel(**opts):
        cfg = config.default_config()
        for e in config.channel(cfg)["apps"]:
            e["enabled"] = e["app"] == "pajamasam"
            if e["app"] == "pajamasam":
                e["options"].update(opts)
        return cfg

    out = os.path.join(work, "pjs", "kitchen")
    render.render(out, channel(), "kitchen", now=NOW)
    lines = manifest(out)
    clip = [t for t in lines if t[0] == "clip"]
    names = [n for n, _, _ in pjs.clips()]
    check(len(clip) == 1 and clip[0][1:3] == ["0", "."] and clip[0][-1] == "random"
          and clip[0][3:-1] == ["pjs_" + n for n in names] and len(names) == 2,
          f"pajamasam: a page that plays a random clip {clip}")
    check(all(os.path.isfile(os.path.join(out, f"pjs_{n}{ext}")) for n in names for ext in (".idx", ".clip"))
          and all(pjs.read_index(os.path.join(out, f"pjs_{n}.idx")) for n in names), "pajamasam: the clips are delivered with it")
    with open(os.path.join(out, "hashes.txt")) as fh:
        hashed = fh.read()
    check(all(f"pjs_{n}.clip" in hashed for n in names), "pajamasam: hashes.txt lists them")
    check(render.problems([" ".join(t) for t in lines], out) == [], "pajamasam: every line is one the engine takes")
    before = snapshot(out)
    time.sleep(0.05)
    changed = render.render(out, channel(), "kitchen", now=NOW)
    diff = sorted(f for f, v in snapshot(out).items() if before.get(f) != v)
    check(not changed and not diff, f"pajamasam: a second render writes nothing {diff[:5]}")
    os.rename(pjs.clip_dir(), pjs.clip_dir() + ".off")
    out2 = os.path.join(work, "pjs", "lounge")
    render.render(out2, channel(), "lounge", now=NOW)
    pages = [t[2] for t in manifest(out2) if t[0] == "page"]
    check(pages == ["pjs_setup.raw"], f"pajamasam: without clips, the page saying where to add them {pages}")
    render.render(out2, channel(setup_page=False), "lounge", now=NOW)
    check([t[2] for t in manifest(out2) if t[0] == "page"] == ["empty.raw"], "pajamasam: ...or no page at all")
    os.rename(pjs.clip_dir() + ".off", pjs.clip_dir())


# ---------------------------------------------------------------- the renderer service
def service(work):
    from neochumby_classic import service as svc
    os.environ["NEOCHUMBY_CLASSIC_CHUMBYS"] = "kitchen, lounge BAD/name"
    s = svc.Service()
    s.update()
    check(sorted(s.due) == ["kitchen", "lounge"], f"service: the Chumbys from the list, bad names dropped {sorted(s.due)}")
    now = time.monotonic()
    check(all(t <= now for t in s.due.values()), "service: new Chumbys are due at once")
    for n in s.due:
        s.due[n] = now + 600
    os.makedirs(os.path.join(work, "refresh"), exist_ok=True)
    open(os.path.join(work, "refresh", "lounge"), "w").close()
    s.update()
    check(s.due["lounge"] <= time.monotonic() < s.due["kitchen"], "service: refresh/<name> makes that Chumby due")
    check(not os.path.exists(os.path.join(work, "refresh", "lounge")), "service: ...and is used up")
    s.due["lounge"] = time.monotonic() + 600
    with open(os.path.join(work, "refresh-request"), "w") as fh:
        fh.write("now\n")
    os.utime(os.path.join(work, "refresh-request"), (time.time() + 5, time.time() + 5))
    s.update()
    check(all(t <= time.monotonic() for t in s.due.values()), "service: refresh-request makes every Chumby due")
    os.environ["NEOCHUMBY_CLASSIC_CHUMBYS"] = "kitchen"
    s.update()
    check(sorted(s.due) == ["kitchen"], "service: a Chumby gone from the list is dropped")
    os.environ.pop("NEOCHUMBY_CLASSIC_CHUMBYS")


# ---------------------------------------------------------------- the web page
class Neochumbyd(socketserver.ThreadingUnixStreamServer):
    """A stand-in for neochumbyd's admin socket (protocol v1, PR #9)."""
    daemon_threads = True                                    # the client keeps its connection open

    def __init__(self, path):
        self.seen = []
        self.paired = {"kitchen": {"start": "classic", "delivery": "sync"}}
        server = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                for raw in self.rfile:
                    w = raw.decode().split()
                    server.seen.append(w)
                    self.wfile.write(("\n".join(server.answer(w)) + "\n").encode())
        super().__init__(path, H)

    def answer(self, w):
        if w == ["paired"]:
            return [f"paired {n} {'ab' * 32} " + " ".join(f"{k}={v}" for k, v in s.items()) for n, s in self.paired.items()] + ["ok"]
        if w == ["pending"]:
            return ["pending 123456 lounge 192.0.2.7 " + "cd" * 32, "ok"]
        if w[:1] == ["check"] and len(w) == 2:
            return ["ok" if w[1] == OWNER else "error wrong token"]
        if w[:1] == ["approve"] and len(w) == 3:
            return ["ok lounge" if w[2] == OWNER else "error wrong token"]
        if w[:1] == ["forget"] and len(w) == 3:
            return ["ok" if w[2] == OWNER else "error wrong token"]
        if w[:1] == ["set"] and len(w) == 4 and "=" in w[2]:          # every change needs the owner's token
            k, v = w[2].split("=", 1)
            if w[3] != OWNER:
                return ["error wrong token"]
            if w[1] not in self.paired:
                return ["error no Chumby paired under that name"]
            self.paired[w[1]][k] = v
            return ["ok"]
        if w[:1] == ["refuse"] and len(w) == 3:
            return ["ok" if w[2] == OWNER else "error wrong token"]
        return ["error unknown command"]


def tree(work):
    """A made-up neochumbyd tree: home and classic may be start apps, doom may not."""
    for app, man in (("home", "title=Apps\nexec=neochumby-home\nstart=true\n"),
                     ("classic", "title=Classic\nexec=neochumby-classic\nstart=true\n"),
                     ("doom", "title=Doom\nexec=neochumby-doom\nbutton=true\n"),
                     ("somafm", "title=SomaFM\nexec=neochumby-somafm\nplayer=true\n"),
                     ("airplay", "title=AirPlay\nexec=neochumby-airplay\nservice=neochumby-airplayd\nplayer=true\n")):
        os.makedirs(os.path.join(work, "tree", "apps", app), exist_ok=True)
        with open(os.path.join(work, "tree", "apps", app, "manifest"), "w") as fh:
            fh.write(man)
    return os.path.join(work, "tree")


class Browser:
    def __init__(self, port, host="kitchen.local"):
        self.port, self.host, self.cookie = port, host, ""

    def go(self, method, path, form=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": f"{self.host}:{self.port}"}
        if self.cookie:
            h["Cookie"] = self.cookie
        body = urllib.parse.urlencode(form) if form is not None else None
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
            h["Origin"] = f"http://{self.host}:{self.port}"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read().decode("utf-8", "replace")
        m = re.match(r"classic_admin=([^;]*)", r.getheader("Set-Cookie") or "")
        if m:
            self.cookie = f"classic_admin={m.group(1)}"
        c.close()
        return r.status, data, r


def web(work, out):
    import argparse as ap
    from http.server import ThreadingHTTPServer

    from neochumby_classic import admin, config, web as page
    path = os.path.join(work, "admin.sock")
    nd = Neochumbyd(path)
    threading.Thread(target=nd.serve_forever, daemon=True).start()
    os.environ["NEOCHUMBY_CLASSIC_ADMIN"] = path
    os.environ["NEOCHUMBY_CLASSIC_TREE"] = tree(work)
    page.ARGS = ap.Namespace(config=config.default_path(), out=os.path.dirname(out), hello="", listen="")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), page.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    b = Browser(srv.server_address[1])
    try:
        check(admin.start_apps() == [("classic", "Classic"), ("home", "Apps")], "only start=true apps are start apps")
        chumbys, pending = admin.listing()
        check([c.name for c in chumbys] == ["kitchen"] and chumbys[0].settings == {"start": "classic", "delivery": "sync"},
              "socket: paired Chumbys and their settings")
        check([(p.name, p.code, p.addr) for p in pending] == [("lounge", "123456", "192.0.2.7")], "socket: requests to pair")
        check(not admin.login("f" * 32) and not admin.login(OWNER + " x") and admin.login(OWNER),
              "socket: only the owner token signs in")
        try:
            admin.approve("123456 x", OWNER)
            check(False, "socket: a code with a space is refused")
        except admin.AdminError:
            check(True, "socket: a code with a space is refused")

        code, body, _ = b.go("GET", "/")
        check(code == 200 and "my channels" in body, "web: the channels page renders")
        for a in config.registry():
            check(f'id="cz-{a["id"]}"' in body, f"web: customize dialog for {a['id']}")
        check('list="dl-weather-station"' in body and 'data-suggest="station"' in body, "web: the weather's station is picked from a list")
        code, body, _ = b.go("GET", "/api/choices?app=weather&key=station&opt.state=NSW")
        check(code == 200 and '"94768"' in body and "Montague" in body, "web: the station list follows the state")
        code, body, _ = b.go("GET", "/api/choices?app=weather&key=name&opt.state=NSW")
        check(code == 404, "web: only listed fields have lists")
        code, body, _ = b.go("GET", "/icon/weather.png")
        check(code == 200 and body.startswith("�PNG"), "web: widget icons")
        code, _, _ = b.go("GET", "/preview/weather_current.raw.png")
        check(code == 200, "web: previews from the renders")
        code, _, _ = Browser(b.port, "evil.example.com").go("GET", "/")
        check(code == 421, "web: a Host that isn't on the LAN is refused")
        code, _, _ = b.go("POST", "/action", {"action": "refresh"}, {"Origin": "http://evil.example.com"})
        check(code == 403, "web: a cross-origin POST is refused")
        code, _, _ = b.go("POST", "/action", {"action": "refresh"})
        check(code == 303 and os.path.exists(os.path.join(work, "refresh-request")), "web: SHOW ON CHUMBY NOW asks for a render")
        code, _, r = b.go("GET", "/chumbys")
        check(code == 303 and r.getheader("Location") == "/login", "web: the Chumbys page needs a sign-in")
        code, _, _ = b.go("POST", "/login", {"token": "f" * 32})
        check(code == 403 and not b.cookie, "web: a wrong token doesn't sign in")
        code, _, r = b.go("POST", "/login", {"token": OWNER})
        check(code == 303 and b.cookie and "HttpOnly" in r.getheader("Set-Cookie") and "SameSite=Strict" in r.getheader("Set-Cookie"),
              "web: the owner token signs in (HttpOnly, SameSite=Strict)")
        code, body, _ = b.go("GET", "/chumbys")
        csrf = re.search(r'name="csrf" value="([^"]+)"', body)
        check(code == 200 and "123456" in body and csrf, "web: the Chumbys page shows the pairing code")
        check('value="classic" selected' in body and 'value="doom"' not in body, "web: the start picker offers only start apps")
        csrf = csrf.group(1) if csrf else ""
        code, _, _ = b.go("POST", "/admin", {"action": "set", "name": "kitchen", "start": "home", "delivery": "nfs"})
        check(code == 403, "web: an admin POST without the CSRF token is refused")
        n = len(nd.seen)
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "set", "name": "kitchen", "start": "doom", "delivery": "nfs"})
        check(code == 400 and not any(w[:1] == ["set"] for w in nd.seen[n:]), "web: a start app that isn't one is refused")
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "set", "name": "kitchen", "start": "home", "delivery": "nfs"})
        check(code == 303 and ["set", "kitchen", f"{admin.START_SETTING}=home", OWNER] in nd.seen
              and ["set", "kitchen", "delivery=nfs", OWNER] in nd.seen,
              "web: start app and delivery are set, with the signed-in owner's token")
        _, body, _ = b.go("GET", "/chumbys")
        check('<select name="player">' in body and ">Default (SomaFM)<" in body and 'value="airplay"' in body
              and 'value="doom">' not in body.split('<select name="player">')[1].split("</select>")[0],
              "web: the player picker offers the default and the tree's players only")
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "set", "name": "kitchen", "start": "home",
                                             "player": "airplay", "delivery": "nfs"})
        check(code == 303 and ["set", "kitchen", "player=airplay", OWNER] in nd.seen and nd.paired["kitchen"]["player"] == "airplay",
              "web: the player is set, with the signed-in owner's token")
        n = len(nd.seen)
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "set", "name": "kitchen", "start": "home",
                                             "player": "doom", "delivery": "nfs"})
        check(code == 400 and not any(w[:1] == ["set"] for w in nd.seen[n:]), "web: a player that isn't one is refused, and nothing is set")
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "approve", "code": "123456"})
        check(code == 303 and ["approve", "123456", OWNER] in nd.seen, "web: approve passes the signed-in owner's token")
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "refuse", "code": "123456"})
        check(code == 303 and ["refuse", "123456", OWNER] in nd.seen, "web: refuse passes it too")
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "forget", "name": "kitchen"})
        check(code == 303 and ["forget", "kitchen", OWNER] in nd.seen, "web: forget passes it too")
        code, body, _ = b.go("GET", "/chumbys?done=" + urllib.parse.quote("<script>"))
        check(code == 200 and "<script>" not in body.split("<script nonce")[0], "web: request text never reaches the page")
        code, _, _ = b.go("POST", "/logout", {"csrf": csrf})
        code, _, _ = b.go("GET", "/chumbys")
        check(code == 303, "web: signed out")
        os.environ["NEOCHUMBY_CLASSIC_OWNER_TOKEN_FILE"] = os.path.join(work, "owner-token")
        with open(os.environ["NEOCHUMBY_CLASSIC_OWNER_TOKEN_FILE"], "w") as fh:
            fh.write("f" * 32 + "\n")
        page._health["at"] = 0
        check("owner token changed" in page.neochumbyd_health(), "web: a stored token that no longer works is reported")
        pajamasam_web(work, b)
    finally:
        srv.shutdown()
        nd.shutdown()
        nd.server_close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out")
    p.add_argument("--screenshots")
    a = p.parse_args()
    work = tempfile.mkdtemp(prefix="classic-smoke-")
    out = a.out or os.path.join(work, "out", "kitchen")
    os.environ["NEOCHUMBY_CLASSIC_STATE"] = work
    os.environ.pop("NEOCHUMBY_CLASSIC_CONFIG", None)
    os.environ["NEOCHUMBY_CLASSIC_ASSETS"] = ""                # the placeholders, as published
    widgets_found()
    renders(work, out, a.screenshots)
    whyalla(work, out)
    weather_places(work)
    flurry(work, out)
    apps_screen(work)
    chumby_info(work, out)
    service(work)
    web(work, out)
    pajamasam_render(work)
    print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
