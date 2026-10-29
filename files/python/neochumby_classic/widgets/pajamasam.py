"""pajamasam: a Pajama Sam screensaver, from the owner's own copies of the games.

Nothing of the games is in this repository, and nothing is fetched. The owner points the
widget's options at their own game folders on this server; the web page's "Convert"
button runs neochumby-pajamasam (from phoozle/neochumby-scummvm), which plays the games
headless and records looping scenes as clips (clip format v1). They go only to this
server's private clip directory (clip_dir(), in the widget's state), or an owner can put
converted clips in an asset directory's pajamasam/ themselves.

With clips: one page that plays a random clip each time it comes on (the engine's `clip`
directive), streamed from the Chumby's copy of the files. The clips are copied into the
Chumby's output directory as pjs_<name>.idx / .clip, and only when they change.
Without clips: a page of our own art saying where to add the files (or none at all, if
the owner switches that off).

  python -m neochumby_classic.widgets.pajamasam icon      # redraw icons/pajamasam.png
"""
import os
import re
import shutil
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

from .. import assets, config
from ..widget import Rendered

W, H = 320, 240
API = 1
APP = {"id": "pajamasam", "name": "Pajama Sam Screensaver", "icon": "icons/pajamasam.png", "seconds": 30,
       "description": "Bring your own: scenes from your own copies of Pajama Sam 1 and 2, looping as a screensaver.",
       "subslides": []}
PATH = r"/[A-Za-z0-9 ._+,@()'-]+(/[A-Za-z0-9 ._+,@()'-]+)*/?"
CONFIG_SCHEMA = [
    {"key": "ps1", "type": "string", "label": "Pajama Sam 1 folder, on the server", "default": "", "optional": True,
     "max_len": 255, "pattern": PATH,
     "help": "Where your copy of the game's files is (Pajama.HE0 and the rest), e.g. /srv/games/pajama1."},
    {"key": "ps2", "type": "string", "label": "Pajama Sam 2 folder, on the server", "default": "", "optional": True,
     "max_len": 255, "pattern": PATH, "help": "PAJAMA2.HE0 and the rest, e.g. /srv/games/pajama2."},
    {"key": "scenes", "type": "number", "label": "Scenes to find", "default": 4, "min": 1, "max": 12, "integer": True,
     "help": "The converter records the scenes it knows, plus this many of the liveliest rooms it finds."},
    {"key": "setup_page", "type": "bool", "label": "Until there are clips, show where to add them", "default": True},
]
JOB = {"label": "Convert", "help": "Records the scenes from the folders above (saved first), about a minute each. "
                                   "The clips stay on this server and go only to your Chumbys."}

# The engine's directive (neochumby-classic engine; see docs/widgets.md): page N plays one of
# the named clips, from DIR relative to the manifest ("." = beside it), a random one each time.
CLIP_DIR = "."
PREFIX = "pjs_"
NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
FORBIDDEN = ("/proc", "/sys", "/dev", "/run", "/etc", "/boot")
EXIT = {   # neochumby-pajamasam convert's exit codes, in words for the web page
    0: "Done: the clips are made. Your Chumbys get them at the next refresh.",
    1: "The converter didn't understand its arguments (a bug here, or a converter of another version).",
    2: "No Pajama Sam game was recognised in the folders given. Check the paths, and that each folder holds the "
       "game's .HE0 file and the rest.",
    3: "The games were recognised, but no scene could be recorded. The progress lines say why.",
    4: "The converter couldn't write its output directory on this server.",
}


# ---------------------------------------------------------------- where things are
def state_dir():
    return os.path.join(config.state_dir(), "widgets", "pajamasam")


def clip_dir():
    """Where the converter writes, and the renderer reads first: private to this server."""
    return os.path.join(state_dir(), "clips")


def converter():
    """The neochumby-pajamasam binary: $NEOCHUMBY_PAJAMASAM, else on the PATH, else None."""
    p = os.environ.get("NEOCHUMBY_PAJAMASAM")
    if p:
        return p if os.path.isfile(p) and os.access(p, os.X_OK) else None
    return shutil.which("neochumby-pajamasam")


def check_folder(path):
    """Why `path` won't do as a game folder, or None. Server-local and absolute, no '..',
    not a system directory, an existing directory this user can read."""
    if not path:
        return None
    if not re.fullmatch(PATH, path) or ".." in path.split("/") or "." in path.split("/") or "//" in path:
        return "must be an absolute folder path on the server, without . or .."
    real = os.path.realpath(path)
    if any(c == f or c.startswith(f + "/") for f in FORBIDDEN for c in (path.rstrip("/"), real)) or real == "/":
        return "isn't a game folder"
    if real == os.path.realpath(state_dir()) or real.startswith(os.path.realpath(state_dir()) + "/"):
        return "is this program's own state"
    if not os.path.isdir(real):
        return "isn't a folder on the server (or this program's user can't see it)"
    if not os.access(real, os.R_OK | os.X_OK):
        return "can't be read by this program's user"
    return None


# ---------------------------------------------------------------- the web page's job (docs/widgets.md)
def job_command(options):
    """The converter's argv for these options (the web page runs it, without a shell). Raises
    ValueError with a reason fit for the page."""
    bin_ = converter()
    if not bin_:
        raise ValueError("The converter isn't installed: build neochumby-pajamasam from "
                         "github.com/phoozle/neochumby-scummvm and put it on the PATH, or set NEOCHUMBY_PAJAMASAM.")
    argv = [bin_, "convert", "--out", clip_dir()]
    for key, flag, label in (("ps1", "--ps1", "Pajama Sam 1"), ("ps2", "--ps2", "Pajama Sam 2")):
        p = (options or {}).get(key) or ""
        why = check_folder(p)
        if why:
            raise ValueError(f"The {label} folder {why}.")
        if p:
            argv += [flag, os.path.realpath(p)]
    if "--ps1" not in argv and "--ps2" not in argv:
        raise ValueError("Give at least one game folder, and save, first.")
    argv += ["--auto", str(int((options or {}).get("scenes") or 4)), "--jobs", "2"]
    os.makedirs(clip_dir(), exist_ok=True)
    return argv


def job_explain(code):
    """The converter's exit code, in words."""
    return EXIT.get(code, f"The converter stopped unexpectedly (exit {code}).")


# ---------------------------------------------------------------- clips
def read_index(path):
    """(fps, frames, data_size) from a clip's index, or None if it isn't clip format v1."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
    except OSError:
        return None
    if len(head) < 32 or head[:8] != b"NCCLIP01":
        return None
    version, w, h, fps, frames, loop, max_record, data_size = struct.unpack("<HHHHIIII", head[8:])
    if version != 1 or (w, h) != (W, H) or not 1 <= fps <= 50 or frames < 1 or loop >= frames:
        return None
    if os.path.getsize(path) != 32 + 8 * (frames + 1):
        return None
    return fps, frames, data_size


def clips(source=None):
    """[(name, title, dir)] of the usable clips in clips.txt, from the converter's directory,
    else the first asset directory with a pajamasam/clips.txt."""
    dirs = [source] if source else [clip_dir()] + [os.path.join(d, "pajamasam") for d in assets.dirs()]
    for d in dirs:
        listing = os.path.join(d, "clips.txt")
        if not os.path.isfile(listing):
            continue
        out = []
        with open(listing, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                t = ln.split(None, 6)
                if len(t) < 6 or not NAME.fullmatch(t[0]):
                    continue
                idx = read_index(os.path.join(d, t[0] + ".idx"))
                data = os.path.join(d, t[0] + ".clip")
                if idx and os.path.isfile(data) and os.path.getsize(data) == idx[2]:
                    out.append((t[0], (t[6] if len(t) > 6 else t[0]).strip()[:60], d))
        return out
    return []


def copy_if_changed(src, dst):
    """Copy a clip file into the output directory, atomically and only when it changed
    (by size and mtime: the copy keeps the source's mtime). True if it wrote."""
    st = os.stat(src)
    try:
        dt = os.stat(dst)
        if dt.st_size == st.st_size and dt.st_mtime_ns == st.st_mtime_ns:
            return False
    except OSError:
        pass
    tmp = os.path.join(os.path.dirname(dst), f".{os.path.basename(dst)}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o640)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


# ---------------------------------------------------------------- our own pictures
def _font(size):
    return ImageFont.truetype(assets.FONTS + "DejaVuSans-Bold.ttf", size)


def night_sky(k=1):
    """A starry night with a crescent moon: our own art, nothing from the games."""
    w, h = W * k, H * k
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=(int(14 + 30 * t), int(10 + 22 * t), int(48 + 60 * t)))
    for i in range(70):                                   # stars: a fixed pattern, so the bytes never change
        x, y = (i * 97 + 31) % W, (i * 53 + 17) % 150
        r = 1 + (i % 3 == 0)
        d.ellipse([(x - r / 2) * k, (y - r / 2) * k, (x + r / 2) * k, (y + r / 2) * k], fill=(255, 250, 220))
    d.ellipse([250 * k, 18 * k, 296 * k, 64 * k], fill=(255, 236, 160))
    d.ellipse([262 * k, 12 * k, 306 * k, 56 * k], fill=(18, 13, 56))          # the sky behind: a crescent
    return img


def setup_page(url):
    img = night_sky(3).resize((W, H), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 84), "Pajama Sam", font=_font(24), fill=(255, 236, 160), anchor="mm")
    for i, ln in enumerate(["Add your Pajama Sam files", "on the web page: the Pajama Sam", "Screensaver's customize."]):
        d.text((W // 2, 124 + i * 20), ln, font=_font(13), fill=(235, 235, 250), anchor="mm")
    addr = re.sub(r"^https?://", "", url or "").rstrip("/")
    if addr:
        d.text((W // 2, 206), addr, font=_font(12), fill=(255, 236, 160), anchor="mm")
    return img


def clip_page():
    """What shows before the engine's first clip frame (and as Home's thumbnail)."""
    img = night_sky(3).resize((W, H), Image.LANCZOS)
    ImageDraw.Draw(img).text((W // 2, 120), "Pajama Sam", font=_font(22), fill=(255, 236, 160), anchor="mm")
    return img


# ---------------------------------------------------------------- render
def page_count(subslide, options):
    return 1


def render(ctx):
    """API 1: a page that plays a random clip, or a page saying where to add the files."""
    if not ctx.slides:
        return Rendered()
    r = Rendered()
    p = ctx.first_page
    found = clips()
    if found:
        names = []
        for name, _title, src in found:
            for ext in (".idx", ".clip"):
                copy_if_changed(os.path.join(src, name + ext), ctx.path(PREFIX + name + ext))
            names.append(PREFIX + name)
        img = clip_page()
        ctx.write565(img, "pjs_page.raw")
        r.lines += [f"page {p} pjs_page.raw {ctx.seconds} noclock noticker",
                    f"clip {p} {CLIP_DIR} " + " ".join(names) + " random"]
        r.titles.append("Pajama Sam")
        r.thumbs.append(img)
        r.facts["pajamasam"] = f"{len(names)} clips"
    elif ctx.options.get("setup_page", True):
        img = setup_page(ctx.config_url)
        ctx.write565(img, "pjs_setup.raw")
        r.lines.append(f"page {p} pjs_setup.raw {ctx.seconds} noclock noticker")
        r.titles.append("Pajama Sam")
        r.thumbs.append(img)
    return r


# ---------------------------------------------------------------- icon
def make_icon():
    """80x80 web page icon: the night sky and moon (our own art)."""
    im = night_sky(4).crop((80 * 4, 0, 320 * 4, 240 * 4)).resize((80, 80), Image.LANCZOS)
    ImageDraw.Draw(im).text((40, 62), "Zz", font=_font(20), fill=(255, 236, 160), anchor="mm")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "pajamasam.png")
    im.save(path, optimize=True)
    print("wrote", path)


if __name__ == "__main__":
    if sys.argv[1:] == ["icon"]:
        make_icon()
    else:
        print(__doc__)
