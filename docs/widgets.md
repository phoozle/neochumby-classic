# The widget API, version 1

A **widget** is one app in a Chumby's channel: Weather, ABC News, Whyalla News, Stock
Quotes, Photos, the Aurora screensaver. Each is a Python module that draws its pages and screens as
pictures, ahead of time, on the server. The Chumby only copies those pictures to its
screen. This page is everything a widget needs to know.

The six built-in widgets live in [`files/python/neochumby_classic/widgets/`](../files/python/neochumby_classic/widgets/)
and are found exactly as an outside widget would be: through an entry point.

## Versioning

- A widget says `API = 1`. The renderer loads only widgets whose `API` it speaks, and logs
  the ones it skips.
- Within version 1, changes are only additions: new optional `APP` keys, new `Context`
  fields, new optional hooks, new directives. A widget written for 1 keeps working.
- Anything else (a field removed or changing meaning, a directive changing shape) is
  version 2, and this page will say how long 1 stays supported.

## The module

```python
from neochumby_classic.widget import Rendered

API = 1
APP = {
    "id": "clock",                       # = the entry point's name; [a-z0-9_]{1,24}
    "name": "Big Clock",                 # on the web page and on Home's card
    "icon": "icons/clock.png",           # 80x80 PNG, relative to this module's directory
    "description": "A clock you can read across the room.",
    "seconds": 12,                       # default seconds per slide
    "subslides": [                       # [] for a widget with one kind of page
        {"id": "time", "label": "The time", "default": True},
        {"id": "date", "label": "The date", "default": False},
    ],
}
CONFIG_SCHEMA = [                        # optional: the widget's options
    {"key": "hours", "type": "choice", "label": "Hours", "default": "12",
     "choices": [["12", "12-hour"], ["24", "24-hour"]]},
]

def page_count(subslide_id, options):   # optional: pages one sub-slide needs (default 1)
    return 1

def render(ctx):                         # required
    ...
    return Rendered(lines=[...], titles=[...], thumbs=[...])
```

### Options (`CONFIG_SCHEMA`)

The web page builds the widget's "customize" form from these fields, and validates every
value against them before the widget sees it (`config.validate`). Every field has `key`,
`type`, `label`, `default`, and optionally `help`.

| `type` | Value | Extra keys |
| --- | --- | --- |
| `string` | a string | `pattern` (a full-match regex), `max_len`, `optional`, `suggest` |
| `number` | a float, or an int with `integer: True` | `min`, `max`, `integer`, `optional` |
| `bool` | `True` / `False` | |
| `choice` | one of the values | `choices: [[value, label], ...]` |
| `list` | a list of strings | `pattern`, `max_items`, `upper` (upper-case them) |
| `image_library` | a list of image ids | `max_items`, `max_file_mb`, `max_total_mb`, and the hooks below |

An `image_library` widget also provides `ingest_image(path) -> id`, `storage_dir()`,
`thumb_path(id)` and optionally `delete_image(id)`. The web page does the upload checks
(size, count, a real Pillow decode); the module turns an upload into its own stored
image. [`config.py`](../files/python/neochumby_classic/config.py) has the details, and
[`widgets/photos.py`](../files/python/neochumby_classic/widgets/photos.py) is the example.

`optional: True` lets a string or number be left blank (`""`, or `None` for a number).

`suggest: True` gives a string field a list to pick from (it can still be typed). The
widget provides `choices(key, options, fetch=False) -> [[value, label], ...]`: the list
for that field, given the options as they stand (the weather's stations follow its
`state`). With `fetch=False` (the page as it is drawn) it must answer from what it has
cached; the dialog's script then asks again with `fetch=True` when it opens and whenever a
`choice` field in it changes, and the widget may fetch then (cache what it fetches, and
keep it polite). Labels are drawn as text only. The Weather widget is the example.

`migrate(options) -> options`, optional: turns options a previous version of the widget
stored into today's, before they are validated (unknown keys are otherwise dropped, and
missing ones get their defaults).

### `page_count` and the slideshow

The renderer plans the slideshow before anything is drawn: apps in channel order, each
app's enabled sub-slides in order, each sub-slide `page_count(subslide_id, options)`
pages (`subslide_id` is `None` for a widget without sub-slides). Whatever doesn't fit in
the engine's 48 pages is cut, and the web page shows what was cut. `render()` is then
told exactly how many pages to draw for each sub-slide.

### `render(ctx)`

Called once per render for every widget **enabled** in the Chumby's channel, even if none
of its pages fit (then `ctx.slides` is `[]`, and it can still write screens that other
pages lead to, or nothing). `ctx` is a `neochumby_classic.widget.Context`:

| Field | What it is |
| --- | --- |
| `out` | the Chumby's output directory |
| `first_page` | the slideshow number of the widget's first page |
| `slides` | `[(subslide_id, pages)]`: what to draw, in order |
| `seconds` | seconds per slide |
| `options` | the validated options |
| `now`, `tz` | the render's time (aware) and the configured time zone |
| `chumby` | the Chumby's name |
| `state_dir` | a directory for the widget's caches (create it if you use it) |
| `config_url` | the web page's address, for "set this up at ..." hints |
| `user_agent` | send it with every fetch |

and helpers: `ctx.write565(img, name)` writes a PIL image as RGB565 if its bytes changed;
`ctx.write(name, data)` the same for raw bytes (`.l8`, `.path`); `ctx.path(name)`;
`ctx.font(file, size)` for the bundled fonts (Liberation Sans, Liberation Sans Narrow,
DejaVu Sans, SN Pro; [`data/fonts/README.md`](../files/python/neochumby_classic/data/fonts/README.md));
`ctx.asset(name)` for an owner-supplied picture, or `None`.

It returns a `Rendered`:

| Field | What it is |
| --- | --- |
| `lines` | the manifest directives it wants (next section) |
| `titles` | one title per slideshow page it added, in order |
| `thumbs` | one 320×240 PIL image per slideshow page (Home's card shows the first) |
| `home_title` | optional: Home's name for it, if not `APP["name"]` ("Whyalla Weather") |
| `facts` | optional: short facts for Settings → Chumby Info (`{"feed": "weather of 28 Oct, 7:30 PM"}`) |
| `latlon` | optional: the place it shows, `(latitude, longitude)`. The first widget in play order that gives one sets the sunrise and sunset that switch night mode, unless the owner set a latitude and longitude (the Weather widget gives its station's) |

**Errors.** Catch what you can (a dead feed should draw the last good data, or a "can't
reach" page). An exception from `render()` fails the whole render, and the Chumby keeps
the last good one: that is the safe failure, but it stops every other widget's updates
too.

## What a widget may write

The screen is 320×240. Coordinates are pixels from the top left. Files are named in the
manifest by their file name only.

**Slideshow pages** (numbered from `ctx.first_page`, in order, one `page` line per title):

| Directive | Meaning |
| --- | --- |
| `page N FILE SECS [noclock] [noticker] [fullframe]` | a 320×240 page; `noclock`/`noticker` hide the shared clock and ticker on it; `fullframe` redraws the whole page every frame (with `bgscroll` or `glow`) |
| `pagenight N FILE` | the page's dark copy, drawn instead in night mode |
| `pagetap N SCREEN` | a tap on the page opens `SCREEN` (tap-through); without it a tap means "next page" |
| `sprite N FILE W H FRAMES X Y FPS` | an animated sprite: frames stacked vertically, `0xF81F` transparent |
| `actor N FILE W H FRAMES FPS PATHFILE NPOINTS` | a sprite moving along a path (`.path`: int16 x,y per frame at 25 fps) |
| `scroller N FILE W H Y SPEED AMP` | a sine-wave text scroller (a keyed strip) |
| `bgscroll N FILE W SPEED` | a seamless W×240 panorama that pans behind the page |
| `glow N FILE W H Y SPEED [FADE_MS PHASE_MS SWAY_PX SWAY_MS [X0 [ON_MS]]]` | a strip of light, added (saturating) at row Y, panning; several make parallax |
| `glowseq N PREFIX NFRAMES FPS R G B BRIGHT` | a looping clip of light frames `PREFIX_000.l8` ... (format in [interface.md](interface.md#the-files)) |
| `logofx N FILE W H X Y [FX [MODE [HOLD]]]` | a logo that builds itself with a demoscene intro (`random`, `fly`, `print`, `wobble`, `zoom`, `drop`, `dissolve`, `raster`, `decrunch`, `spin`, `octopus`) |

**Screens** (interactive, outside the slideshow):

| Directive | Meaning |
| --- | --- |
| `screen NAME FILE` | a 320×240 screen; `NAME_night`, if there is one, is shown instead in night mode |
| `pane SCREEN FILE W H X Y VAR FRAMES` | a picture on the screen showing the frame for state variable `VAR` |
| `hot SCREEN X Y W H ACTION [ARG]` | a hot zone: a tap there does `ACTION` |
| `slider SCREEN EMPTY FULL KNOB W H X Y T0 T1 KW KH` | the volume slider |

Actions: `slideshow` (back to the slideshow, to the page a `pagetap` came from),
`screen NAME`, `chumby` (leave for the stock Chumby software), `music_toggle`,
`station N`, `next_station`, `night`, `nightmode`, `pin`, `next_app`, `prev_app`,
`secs_up`, `secs_down`. State variables for panes: `music`, `station`, `night`,
`nightmode`, `pin`, `secs`, `app`.

**Shared by the whole slideshow** (one widget only: the first in play order that writes
one wins, and later ones are dropped): `clock FILE CELLW CELLH RIGHT Y W0..W14`,
`clocknight FILE`, `ticker FILE W H Y SPEED`, `tickernight FILE`. The Weather widget
writes these.

**Not for widgets**: `sun`, `station`, `secsopts` and `pageapp` belong to the renderer,
which also draws Home, Settings, Music and the QR code screen. Lines starting with `#` are
comments. The renderer refuses (fails the render on) any other directive, a page line out
of order, a page-numbered directive for another widget's page, an unsafe file name or a
file that wasn't written.

### Tap-through screens

A slideshow page can lead to screens: `pagetap N SCREEN` opens `SCREEN`, and the screen's
`hot` zones lead on (`screen OTHER`) or back (`slideshow`). Every screen a `hot` or
`pagetap` names must exist. Give each screen a `NAME_night` copy if it is bright: the
engine shows it in night mode. Stocks (`stocks_list` → `stocks_d0` ...) and News
(`news_nat_l0` → `news_nat_s0_p0` ...) are the examples.

## The rendering contract

The Chumby fetches only what changed, and its engine keeps a few megabytes of pictures in
memory. So:

- **Stable names.** The same picture gets the same name every render: name by slot
  (`stocks_d2.raw`, `news_nat_s1_p0.raw`), never by content, time or a counter. Start
  every name with the widget's id (`clock_time.raw`, screen `clock_settings`), or a
  short form of it when the id is long (Whyalla News uses `wn_`).
- **Short screen names.** A screen name is 1-23 letters, digits and `_`, its `_night`
  copy's included (the engine keeps 24 bytes for it), so keep the day name to 17. File
  names may be up to 63 characters. The renderer refuses a longer screen name.
- **Write if changed.** Write through `ctx.write565` / `ctx.write`. They leave an
  identical file alone and replace a changed one atomically (interface.md).
- **No per-run times in big files.** A clock reading "rendered 7:42" or an age like "3 min
  ago" makes the page new on every render, and the Chumby fetches it again every ten
  minutes for nothing. Show the data's own time ("as of 4:00 PM", "obs 19:30"), or leave
  it to the engine's clock. The same data must give the same bytes: no randomness, or
  seed it from the data.
- **Per-page memory budget: 2 MB.** The files one page or screen needs at once (its
  picture, `pagenight`, sprites, glows, panorama, three `glowseq` frames, panes, slider)
  should total at most 2 MB. The engine's cache is 4 MB and must hold the page on screen
  and the next one it prefetches. The aurora, the heaviest built-in page, needs about
  1.5 MB. The renderer logs a page over budget.
- **The engine's limits** are shared by all widgets in a channel, so take only what you
  need. A render over one is refused, whole. They are one block of constants,
  [`limits.py`](../files/python/neochumby_classic/limits.py), which the smoke test checks
  against this table:

  | Directive | In the engine | Limit |
  | --- | --- | --- |
  | `page` | `MAXP`: slideshow pages | 48 |
  | `screen` | `MAXSCR`: screens (each `_night` copy counts) | 160 |
  | `pane` | `MAXPANE` | 128 |
  | `hot` | `MAXHOT`: hot zones | 768 |
  | `sprite` | `MAXS` | 64 |
  | `actor` | `MAXA` | 64 |
  | `glow` | `MAXGLOW`: light layers | 16 |
  | `glowseq` | `MAXGSQ`: light clips (`MAXGSQ_FRAMES`: 1000 frames each) | 4 |
  | `station` | `MAXST`: music stations | 16 |
  | `sun` | `MAXSUN`: sunrise/sunset days | 16 |
  | (files) | `MAXASSET`: different files the manifest names | 640 |

  With every built-in widget on, a channel uses 16 pages, 107 screens, 19 panes and 481 hot zones.
- **Untrusted content stays pixels.** Text and pictures from the internet are only ever
  drawn. Names, directives and paths never come from fetched content.
- **Fetch politely.** Send `ctx.user_agent`, use timeouts, cache in `ctx.state_dir`, and
  respect the source's terms.

## A widget in its own repository

A widget is an ordinary Python package with one entry point in the group
`neochumby_classic.widgets`:

```
classic-clock/
  pyproject.toml
  classic_clock/
    __init__.py          # the widget: API, APP, CONFIG_SCHEMA, render
    icons/clock.png
```

```toml
[project]
name = "classic-clock"
version = "1.0.0"
dependencies = ["neochumby-classic>=0.1"]

[project.entry-points."neochumby_classic.widgets"]
clock = "classic_clock"

[tool.setuptools.package-data]
classic_clock = ["icons/*.png"]
```

Install it into the renderer's environment, **pinned to a commit**, so what runs is what
you reviewed:

```sh
sudo ./install.sh --widget git+https://github.com/you/classic-clock@0123456789abcdef0123456789abcdef01234567
```

`install.sh` accepts only a full 40-character commit hash, records the widget in
`/etc/neochumby-classic/widgets.txt` (so re-running install.sh keeps it), installs it with
pip into the venv and restarts the services. The new widget then appears under "add
apps" on the web page.

The built-in widgets use the same mechanism: this repository's
[`pyproject.toml`](../pyproject.toml) declares them as entry points. (When the code runs
from a source tree without being installed, `widget.BUILTIN` stands in for that table,
and the smoke test checks the two agree.)

A widget runs inside the renderer with its user's permissions. Install only widgets you
trust, at commits you have read.
