"""service: the renderer service. Renders every paired Chumby's channel, every 10 minutes
and on request, each into its own directory (docs/interface.md).

Usage: neochumby-classic-renderer [--once]

  --once     render every Chumby once, then exit

Which Chumbys: NEOCHUMBY_CLASSIC_CHUMBYS (names, separated by spaces or commas), else the
paired ones, asked of neochumbyd's admin socket (admin.py) every 30 seconds. If neochumbyd
can't be asked, the last list it gave is kept.

When:
  - a Chumby that is new to the list: at once;
  - every NEOCHUMBY_CLASSIC_EVERY seconds (default 600, at least 60);
  - <state>/refresh-request touched (the web page does, on every save): every Chumby, now;
  - <state>/refresh/<name> created: that Chumby, now (the file is then removed).

Each render runs as its own process (render.py), so a stuck fetch is killed after
RENDER_TIMEOUT and one bad render can't take the service down. Facts about a Chumby for its
Settings screens are read from <state>/info/<name> if that file exists.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import time

from . import admin, config

RENDER_TIMEOUT = 300           # seconds; a render that takes longer is stuck, and is killed
PAIRED_EVERY = 30              # seconds between asking neochumbyd who is paired
POLL = 1.0
NAME = re.compile(config.CHUMBY_NAME)


def log(msg):
    print(f"neochumby-classic-renderer: {msg}", file=sys.stderr, flush=True)


def every():
    try:
        return max(60, int(os.environ.get("NEOCHUMBY_CLASSIC_EVERY") or 600))
    except ValueError:
        return 600


def out_root():
    return os.environ.get("NEOCHUMBY_CLASSIC_OUT") or os.path.join(config.state_root(), "out")


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


class Service:
    def __init__(self):
        self.state = config.state_dir()
        self.out = out_root()
        self.interval = every()
        self.chumbys = []
        self.settings = {}                              # name -> neochumbyd's settings for it (music=, ...)
        self.asked = 0.0
        self.unreachable = False
        self.due = {}                                   # name -> when its next render is due
        self.refresh = mtime(os.path.join(self.state, "refresh-request"))
        self.child = None
        self.stopping = False

    # ------------------------------------------------------------ who
    def list_chumbys(self):
        static = os.environ.get("NEOCHUMBY_CLASSIC_CHUMBYS")
        if static is not None:
            return sorted({n for n in re.split(r"[\s,]+", static) if NAME.fullmatch(n)})
        now = time.monotonic()
        if self.asked and now - self.asked < PAIRED_EVERY:
            return self.chumbys
        self.asked = now
        try:
            got = admin.paired()
            names = sorted(c.name for c in got)
            self.settings = {c.name: c.settings for c in got}
            if self.unreachable:
                log("neochumbyd answers again")
            self.unreachable = False
            return names
        except admin.AdminError as e:
            if not self.unreachable:
                log(f"can't list the paired Chumbys ({e}); keeping the last list ({len(self.chumbys)})")
            self.unreachable = True
            return self.chumbys

    # ------------------------------------------------------------ when
    def update(self):
        names = self.list_chumbys()
        now = time.monotonic()
        for n in names:
            self.due.setdefault(n, now)                  # new: at once
        for n in list(self.due):
            if n not in names:
                del self.due[n]
        self.chumbys = names
        rq = mtime(os.path.join(self.state, "refresh-request"))
        if rq != self.refresh:
            self.refresh = rq
            if rq is not None:
                log("refresh requested")
                for n in self.due:
                    self.due[n] = now
        rdir = os.path.join(self.state, "refresh")
        for n in list(self.due):
            p = os.path.join(rdir, n)
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
                self.due[n] = now

    # ------------------------------------------------------------ one render
    def render(self, name):
        out = os.path.join(self.out, name)
        cmd = [sys.executable, "-m", "neochumby_classic.render", out, "--chumby", name,
               "--config", config.default_path()]
        info = os.path.join(self.state, "info", name)
        if os.path.isfile(info):
            cmd += ["--info", info]
        music = self.settings.get(name, {}).get(admin.MUSIC_SETTING, "")
        if NAME.fullmatch(music):
            cmd += ["--music", music]
        start = time.monotonic()
        try:
            self.child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=sys.stderr)
            status = self.child.wait(timeout=RENDER_TIMEOUT)
            if status and not self.stopping:
                log(f"render for {name} failed (exit {status}); its last output stays")
        except subprocess.TimeoutExpired:
            log(f"render for {name} took over {RENDER_TIMEOUT} s; stopped")
            self.child.kill()
            self.child.wait()
        except OSError as e:
            log(f"can't start a render: {e}")
        finally:
            self.child = None
        return time.monotonic() - start

    def run(self, once=False):
        os.makedirs(self.out, exist_ok=True)
        log(f"rendering into {self.out} every {self.interval} s; config {config.default_path()}")
        if once:
            for n in self.list_chumbys():
                self.render(n)
            return 0
        while not self.stopping:
            self.update()
            now = time.monotonic()
            todo = sorted((t, n) for n, t in self.due.items() if t <= now)
            for _, n in todo:
                if self.stopping:
                    break
                self.render(n)
                self.due[n] = time.monotonic() + self.interval
            time.sleep(POLL)
        return 0

    def stop(self, *_):
        self.stopping = True
        if self.child is not None:
            try:
                self.child.terminate()
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="neochumby-classic-renderer", description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="render every Chumby once, then exit")
    a = ap.parse_args(argv)
    svc = Service()
    signal.signal(signal.SIGTERM, svc.stop)
    signal.signal(signal.SIGINT, svc.stop)
    return svc.run(once=a.once)


if __name__ == "__main__":
    sys.exit(main())
