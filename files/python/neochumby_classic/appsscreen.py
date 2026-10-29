"""appsscreen: Home -> APPS on the Chumby. The neochumby apps that run full screen (DOOM,
QUAKE, AirPlay, ...), as tiles; a tap on one opens it.

The list comes from neochumbyd's app tree on this server (admin.tree_dir()): every
apps/<name>/manifest with a title and an exec, except this experience (classic) and the
stock app list (home). A tile shows the app's icon.png (in its directory, where the tree
puts its files/) or, without one, its initial on a square of its colour, and its title in
its colour. Each tile is a hot zone with the engine's action "app NAME", which the engine
passes to the session as "open NAME" (the session refuses an app it doesn't have).
Music apps (music = true) come first, with a note badge; the Chumby's own music app (its
music= setting, else the session's default) has a star. Home's MUSIC opens that app, and
this screen when there is none.

Screens (stable names, a night copy each): apps_p0, apps_p1, ... six tiles a page, with
PREV / NEXT between pages and DONE back to Home.

    lines = appsscreen.render(out, rgb565, appsscreen.full_apps(), default="somafm")
    lines += appsscreen.render(out, rgb565, apps, night=True, default="somafm")
"""
import math
import os
import re

from PIL import Image

from . import admin, screens
from .screens import SS

SKIP = {"classic", "home"}            # this experience, and the stock app list
COLS, ROWS = 3, 2
PER_PAGE = COLS * ROWS
MAX_APPS = 60                         # ten pages: well inside the engine's limits
TILE_W, TILE_H = 92, 80
GRID_X, GRID_Y = 14, 30
ICON = 40
MAX_ICON_PIXELS = 1024 * 1024
DEFAULT_COLOUR = (220, 160, 40)       # the session's, for an app without one
PREV_BOX, NEXT_BOX = (10, 206, 74, 230), (82, 206, 146, 230)


def colour(s):
    """A manifest's colour=RRGGBB, else the session's default."""
    s = (s or "").strip().lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) if re.fullmatch(r"[0-9A-Fa-f]{6}", s) else DEFAULT_COLOUR


def full_apps(tree=None):
    """[{name, title, colour, icon}] for the apps in neochumbyd's tree that can be opened full
    screen, by title. icon is a path or None."""
    tree = tree or admin.tree_dir()
    try:
        names = sorted(os.listdir(os.path.join(tree, "apps")))
    except OSError:
        return []
    out = []
    for name in names:
        if name in SKIP or not admin.NAME.fullmatch(name):
            continue
        d = os.path.join(tree, "apps", name)
        try:
            with open(os.path.join(d, "manifest"), encoding="utf-8", errors="replace") as fh:
                m = {k.strip(): v.strip() for k, v in (ln.rstrip("\n").split("=", 1) for ln in fh if "=" in ln)}
        except OSError:
            continue
        title = "".join(ch for ch in m.get("title", "") if ch.isprintable()).strip()[:40]
        if not title or not m.get("exec"):
            continue
        icon = next((p for p in (os.path.join(d, "icon.png"), os.path.join(d, "files", "icon.png")) if os.path.isfile(p)), None)
        out.append({"name": name, "title": title, "colour": colour(m.get("colour")), "icon": icon,
                    "music": m.get("music", "").lower() == "true"})
    return sorted(out, key=lambda a: (not a["music"], a["title"].lower(), a["name"]))[:MAX_APPS]


def _icon(path):
    """An app's icon.png, fitted into ICON x ICON (supersampled), or None if it won't do."""
    try:
        with Image.open(path) as im:
            if im.width * im.height > MAX_ICON_PIXELS:
                return None
            im = im.convert("RGBA")
        im.thumbnail((ICON * SS, ICON * SS), Image.LANCZOS)
        return im
    except Exception:
        return None


def star(d, cx, cy, r, fill):
    pts = [(cx + (r if k % 2 == 0 else r * .45) * math.sin(k * math.pi / 5),
            cy - (r if k % 2 == 0 else r * .45) * math.cos(k * math.pi / 5)) for k in range(10)]
    d.polygon(pts, fill=fill)


def tile(img, d, a, x, y, default=False):
    """One app's tile at (x, y), on the supersampled screen: a note badge if it plays music,
    a star if it's the Chumby's music app."""
    T, f = screens.T, screens.f
    d.rounded_rectangle([x * SS, y * SS, (x + TILE_W) * SS, (y + TILE_H) * SS], radius=10 * SS, fill=T["box"],
                        outline=T["frame1"], width=SS)
    cx, cy = (x + TILE_W / 2) * SS, (y + 6 + ICON / 2) * SS
    pic = _icon(a["icon"]) if a["icon"] else None
    if pic:
        img.paste(pic, (int(cx - pic.width / 2), int(cy - pic.height / 2)), pic)
    else:                                               # the generic glyph: its initial on its colour
        r = ICON / 2 * SS
        d.rounded_rectangle([cx - r, cy - r, cx + r, cy + r], radius=8 * SS, fill=a["colour"])
        d.text((cx, cy), a["title"][:1].upper(), font=f(screens.VERDB, 20), fill=(255, 255, 255), anchor="mm")
    fnt, title = f(screens.VERDB, 8), a["title"]
    while fnt.getlength(title) > (TILE_W - 8) * SS and len(title) > 2:
        title = title[:-2].rstrip() + "…"
    ink = a["colour"]
    if screens.T is screens.NIGHT:                      # on the night navy a dark colour would vanish: lighten it
        ink = tuple(int(v + (255 - v) * 0.35) for v in ink)
    d.text((cx, (y + TILE_H - 13) * SS), title, font=fnt, fill=ink, anchor="mm")
    if a.get("music"):
        screens.icon(d, "note", (x + 12) * SS, (y + 12) * SS, 13 * SS, T["sub"])
    if default:
        star(d, (x + TILE_W - 12) * SS, (y + 12) * SS, 7 * SS, (240, 180, 30))


def render(out, rgb565, apps, night=False, default=None):
    """The APPS screens; default: the Chumby's music app (starred), or None."""
    saved = screens.T                                   # screens' theme is a module global: restore it
    screens.T = screens.NIGHT if night else screens.LIGHT
    try:
        return _render(out, rgb565, apps, "_night" if night else "", default)
    finally:
        screens.T = saved


def _render(out, rgb565, apps, sfx, default):
    T, f = screens.T, screens.f
    pages = max(1, -(-len(apps) // PER_PAGE))
    lines = []
    for p in range(pages):
        scr = f"apps_p{p}{sfx}"
        img, d = screens.panel_background("Apps", "grid")
        mine = apps[p * PER_PAGE:(p + 1) * PER_PAGE]
        if not apps:
            d.text((160 * SS, 100 * SS), "No apps to open yet", font=f(screens.VERDB, 11), fill=T["text"], anchor="mm")
            d.text((160 * SS, 122 * SS), "Apps installed in neochumbyd's tree show here.", font=f(screens.VERD, 8),
                   fill=T["sub"], anchor="mm")
        for k, a in enumerate(mine):
            tile(img, d, a, GRID_X + (k % COLS) * (TILE_W + 8), GRID_Y + (k // COLS) * (TILE_H + 6), a["name"] == default)
        if p > 0:
            screens.pill(d, PREV_BOX, "PREV")
        if p < pages - 1:
            screens.pill(d, NEXT_BOX, "NEXT")
        if pages > 1:
            d.text((((NEXT_BOX[2] + screens.DONE_BOX[0]) / 2) * SS, 218 * SS), f"{p + 1} of {pages}",
                   font=f(screens.VERD, 8), fill=T["hint"], anchor="mm")
        screens.done_pill(d)
        _, ls = screens.screen_lines(out, rgb565, img, scr)
        lines += ls
        for k, a in enumerate(mine):
            x, y = GRID_X + (k % COLS) * (TILE_W + 8), GRID_Y + (k // COLS) * (TILE_H + 6)
            lines.append(f"hot {scr} {x} {y} {TILE_W} {TILE_H} app {a['name']}")
        for box, to in ((PREV_BOX, p - 1), (NEXT_BOX, p + 1)):
            if 0 <= to < pages:
                lines.append(f"hot {scr} {box[0] - 4} {box[1] - 6} {box[2] - box[0] + 8} 34 screen apps_p{to}")
        b = screens.DONE_BOX
        lines.append(f"hot {scr} {b[0] - 6} {b[1] - 6} {b[2] - b[0] + 12} 40 screen home")
    return lines
