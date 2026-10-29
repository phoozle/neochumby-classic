"""admin: this program's one way into neochumbyd: its admin socket.

The web page lists the paired Chumbys and the ones asking to pair, approves or refuses a
request, forgets a Chumby, and sets a Chumby's start app (start=) and delivery
(delivery=nfs|sync). The renderer lists the paired Chumbys. Only this module talks to
neochumbyd, and only over the socket, so the socket's protocol is the whole contract.

The socket (neochumbyd's admin.sock, protocol v1): a local Unix stream socket. A request
is one line of space-separated words; the answer is data lines, then "ok", "ok WORD" or
"error MESSAGE". One connection is kept open and used for one request at a time.

    paired               -> paired NAME KEYHEX [k=v ...]  (per Chumby) ... ok
    pending              -> pending CODE NAME ADDRESS KEYHEX (per request) ... ok
    approve CODE TOKEN   -> ok NAME | error ...
    refuse CODE TOKEN    -> ok | error ...
    forget NAME TOKEN    -> ok | error ...
    set NAME KEY=VALUE TOKEN -> ok | error ...  (KEY= with no value removes it)
    check TOKEN          -> ok | error wrong token

TOKEN is the server owner's token (`neochumbyd token` prints it). neochumbyd requires it
on every change (approve, refuse, forget, set), so a fault in this program can't change
a Chumby by itself: the page passes the token the owner typed at sign-in.

Start apps are the apps in neochumbyd's tree whose manifest says start=true (proposal
0002): only those are offered.

Environment:
  NEOCHUMBY_CLASSIC_ADMIN   the socket (default /var/lib/neochumbyd/admin.sock; NEOCHUMBY_ADMIN,
                            as neochumbyd names it for its own daemons, is used if set)
  NEOCHUMBY_TREE            neochumbyd's app tree, as neochumbyd gives it to its daemons
  NEOCHUMBY_CLASSIC_TREE    else this (default /var/lib/neochumbyd/tree)

As a command, for install.sh and for checking a setup by hand:
  neochumby-classic-admin paired      the paired Chumbys, one per line
  neochumby-classic-admin check       reads a token on stdin; exit 0 if neochumbyd takes it
  neochumby-classic-admin starts      the apps that can be start apps
"""
import os
import re
import socket
import sys
import threading
from collections import namedtuple

NAME = re.compile(r"[a-z0-9_-]{1,32}")                 # neochumby_proto::valid_name
CODE = re.compile(r"[0-9]{6}")
OWNER_TOKEN = re.compile(r"[0-9a-f]{32}")             # neochumbyd's owner-token
VALUE = re.compile(r"[A-Za-z0-9.:/_-]{0,128}")       # what neochumbyd accepts as a setting's value
DELIVERY = ("nfs", "sync")
# The Chumby setting that names the app it starts in, and the manifest key (with "true")
# that lets an app be one (proposal 0002). Only these lines name them.
START_SETTING = "start"
START_MANIFEST_KEY = "start"
MUSIC_SETTING = "music"                       # a Chumby's music app (neochumbyd set NAME music=APP)
MUSIC_MANIFEST_KEY = "music"                  # music = true: an app that plays music (the session's flag)
DEFAULT_MUSIC = "somafm"                      # the session's default, if it's in the tree
DEFAULT_SOCKET = "/var/lib/neochumbyd/admin.sock"
DEFAULT_TREE = "/var/lib/neochumbyd/tree"

Chumby = namedtuple("Chumby", "name key settings")   # settings: {key: value}, e.g. start, delivery
Pending = namedtuple("Pending", "name code addr key")


class AdminError(Exception):
    """What went wrong, in words fit for the page."""


class Unreachable(AdminError):
    """The socket isn't there, or this user may not use it."""


def socket_path():
    return os.environ.get("NEOCHUMBY_CLASSIC_ADMIN") or os.environ.get("NEOCHUMBY_ADMIN") or DEFAULT_SOCKET


def tree_dir():
    """neochumbyd's app tree: the start apps (the web page) and Home -> APPS (appsscreen.py)."""
    return os.environ.get("NEOCHUMBY_TREE") or os.environ.get("NEOCHUMBY_CLASSIC_TREE") or DEFAULT_TREE


# ---------------------------------------------------------------- start apps, from the tree
def manifest(app):
    try:
        with open(os.path.join(tree_dir(), "apps", app, "manifest"), encoding="utf-8", errors="replace") as fh:
            return dict(ln.strip().split("=", 1) for ln in fh if "=" in ln)
    except OSError:
        return {}


def start_apps():
    """[(app, title)] for every app in the tree that may be a start app."""
    try:
        apps = sorted(os.listdir(os.path.join(tree_dir(), "apps")))
    except OSError:
        return []
    out = []
    for app in apps:
        if NAME.fullmatch(app):
            m = manifest(app)
            if m.get(START_MANIFEST_KEY, "").strip().lower() == "true":
                out.append((app, m.get("title") or app))
    return out


# ---------------------------------------------------------------- the socket
class Client:
    """One connection to the admin socket, kept open and used by one request at a time,
    made again if it breaks. Every word sent has been checked against its pattern first,
    so no request can carry a space or a newline of its own."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.f = None

    def ask(self, *words):
        line = " ".join(words)
        if any(not w or any(c.isspace() for c in w) for w in words) or len(line) > 1000:
            raise AdminError("bad request")
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.f is None:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        s.settimeout(20)
                        s.connect(self.path)
                        self.f = s.makefile("rw", encoding="utf-8", newline="\n")
                    self.f.write(line + "\n")
                    self.f.flush()
                    out = []
                    while True:
                        ln = self.f.readline()
                        if not ln:
                            raise OSError("neochumbyd closed the admin socket")
                        ln = ln.rstrip("\n")
                        if ln == "ok" or ln.startswith("ok "):
                            return out, ln[3:]
                        if ln.startswith("error"):
                            raise AdminError(ln[6:][:200] or "neochumbyd refused")
                        out.append(ln)
                except OSError as e:
                    self.f = None
                    if attempt:
                        raise Unreachable(f"can't reach neochumbyd's admin socket ({self.path}): {e}") from None


_client = None


def client():
    global _client
    path = socket_path()
    if _client is None or _client.path != path:
        _client = Client(path)
    return _client


# ---------------------------------------------------------------- what the page and the renderer call
def paired():
    """[Chumby] for every paired Chumby."""
    out = []
    for ln in client().ask("paired")[0]:
        t = ln.split()
        if len(t) >= 3 and t[0] == "paired" and NAME.fullmatch(t[1]):
            out.append(Chumby(t[1], t[2], dict(kv.split("=", 1) for kv in t[3:] if "=" in kv)))
    return out


def pending():
    """[Pending] for every Chumby asking to pair."""
    out = []
    for ln in client().ask("pending")[0]:
        t = ln.split()                                  # pending CODE NAME ADDRESS KEYHEX
        if len(t) >= 5 and t[0] == "pending" and CODE.fullmatch(t[1]):
            out.append(Pending(t[2], t[1], t[3], t[4]))
    return out


def listing():
    """(paired Chumbys, requests to pair)."""
    return paired(), pending()


def login(token):
    """True if `token` is the server owner's token, as neochumbyd checks it. Raises
    Unreachable if neochumbyd can't be asked."""
    token = (token or "").strip()
    if not OWNER_TOKEN.fullmatch(token):
        return False
    try:
        client().ask("check", token)
        return True
    except Unreachable:
        raise
    except AdminError:
        return False


def approve(code, token):
    """Pairs the Chumby asking with `code`; `token` is the signed-in owner's token."""
    if not CODE.fullmatch(code or ""):
        raise AdminError("a pairing code is six digits")
    if not OWNER_TOKEN.fullmatch(token or ""):
        raise AdminError("sign in again")
    return client().ask("approve", code, token)[1]


def refuse(code, token):
    if not CODE.fullmatch(code or ""):
        raise AdminError("a pairing code is six digits")
    if not OWNER_TOKEN.fullmatch(token or ""):
        raise AdminError("sign in again")
    client().ask("refuse", code, token)


def forget(name, token):
    if not NAME.fullmatch(name or ""):
        raise AdminError("not a Chumby's name")
    if not OWNER_TOKEN.fullmatch(token or ""):
        raise AdminError("sign in again")
    client().ask("forget", name, token)


def set_setting(name, key, value, token):
    """set NAME KEY=VALUE TOKEN: every change over the socket needs the signed-in owner's token."""
    if not NAME.fullmatch(name or "") or not re.fullmatch(r"[a-z_]{1,32}", key) or not VALUE.fullmatch(value or ""):
        raise AdminError("not a valid setting")
    if not OWNER_TOKEN.fullmatch(token or ""):
        raise AdminError("sign in again")
    client().ask("set", name, f"{key}={value}", token)


def set_start(name, app, token):
    """A Chumby's start app; "" goes back to neochumbyd's default."""
    if app and app not in [a for a, _ in start_apps()]:
        raise AdminError("not a start app in this server's tree")
    set_setting(name, START_SETTING, app, token)


def music_apps():
    """[(app, title)] for every app in the tree that plays music and can be opened."""
    try:
        apps = sorted(os.listdir(os.path.join(tree_dir(), "apps")))
    except OSError:
        return []
    out = []
    for app in apps:
        if NAME.fullmatch(app):
            m = manifest(app)
            if m.get(MUSIC_MANIFEST_KEY, "").strip().lower() == "true" and m.get("exec") and m.get("title"):
                out.append((app, m["title"]))
    return out


def default_music(setting, apps=None):
    """The music app a Chumby opens: its music= setting if that's a music app here, else the
    session's default (somafm) if it's here, else the first by name, else None."""
    names = [a for a, _ in (music_apps() if apps is None else apps)]
    for cand in (setting, DEFAULT_MUSIC):
        if cand and cand in names:
            return cand
    return names[0] if names else None


def set_music(name, app, token):
    """A Chumby's music app; "" goes back to the default. Needs the owner's token."""
    if app and app not in [a for a, _ in music_apps()]:
        raise AdminError("not a music app in this server's tree")
    set_setting(name, MUSIC_SETTING, app, token)


def set_delivery(name, how, token):
    if how and how not in DELIVERY:
        raise AdminError("delivery is nfs or sync")
    set_setting(name, "delivery", how, token)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ["paired"]:
            for c in paired():
                print(c.name, " ".join(f"{k}={v}" for k, v in sorted(c.settings.items())))
            return 0
        if argv == ["check"]:
            ok = login(sys.stdin.readline())
            print("ok" if ok else "neochumbyd didn't take that token", file=sys.stderr)
            return 0 if ok else 1
        if argv == ["starts"]:
            for app, title in start_apps():
                print(app, title)
            return 0
    except Unreachable as e:
        print(f"neochumby-classic-admin: {e}", file=sys.stderr)
        return 2
    except AdminError as e:
        print(f"neochumby-classic-admin: {e}", file=sys.stderr)
        return 3
    print("usage: neochumby-classic-admin paired | check (token on stdin) | starts", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
