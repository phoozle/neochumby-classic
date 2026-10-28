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
    check(ids == ["weather", "news", "stocks", "photos", "aurora"], f"the five built-in widgets are found: {ids}")
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
    config.save(cfg)
    kept, dropped = config.plan(config.load())
    check(not dropped, f"plan fits the page limit ({sum(s['pages'] for s in kept)} pages)")

    changed = render.render(out, config.load(), "kitchen", now=NOW)
    check(changed, "the first render writes")
    lines = manifest(out)
    pages = [t for t in lines if t[0] == "page"]
    check(len(pages) == sum(s["pages"] for s in kept), f"{len(pages)} slideshow pages, as planned")
    for app, prefix in (("weather", "weather_"), ("news", "news_slide_"), ("stocks", "stocks_"), ("photos", "photo"),
                        ("aurora", "aurora_")):
        check(any(t[2].startswith(prefix) for t in pages), f"{app} has a page")
    screens = {t[1] for t in lines if t[0] == "screen"}
    for want in ("home", "home_night", "settings", "timing", "network", "about", "configure", "music", "confirm",
                 "stocks_list", "news_nat_l0"):
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
                          ("home", preview.screen(out, "home")),
                          ("settings", preview.screen(out, "settings"))):
            img.resize((640, 480), resample=0).save(os.path.join(shots, f"{name}.png"), optimize=True)
        print(f"      screenshots in {shots}")


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
        if w[:1] == ["set"] and len(w) == 3 and "=" in w[2]:
            k, v = w[2].split("=", 1)
            if w[1] not in self.paired:
                return ["error no Chumby paired under that name"]
            self.paired[w[1]][k] = v
            return ["ok"]
        if w[:1] == ["refuse"] and len(w) == 2:
            return ["ok"]
        return ["error unknown command"]


def tree(work):
    """A made-up neochumbyd tree: home and classic may be start apps, doom may not."""
    for app, man in (("home", "title=Apps\nexec=neochumby-home\nstart=true\n"),
                     ("classic", "title=Classic\nexec=neochumby-classic\nstart=true\n"),
                     ("doom", "title=Doom\nexec=neochumby-doom\nbutton=true\n")):
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
        check(code == 303 and ["set", "kitchen", f"{admin.START_SETTING}=home"] in nd.seen and ["set", "kitchen", "delivery=nfs"] in nd.seen,
              "web: start app and delivery are set")
        code, _, _ = b.go("POST", "/admin", {"csrf": csrf, "action": "approve", "code": "123456"})
        check(code == 303 and ["approve", "123456", OWNER] in nd.seen, "web: approve passes the signed-in owner's token")
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
    service(work)
    web(work, out)
    print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
