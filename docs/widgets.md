# The widget API, version 1

A **widget** is one app in a Chumby's channel: Weather, ABC News, Whyalla News, Stock
Quotes, Photos, the Aurora screensaver, the Flurry screensaver, the Pajama Sam screensaver. Each is a Python module that draws its pages and screens as
pictures, ahead of time, on the server. The Chumby only copies those pictures to its
screen. This page is everything a widget needs to know.

The eight built-in widgets live in [`files/python/neochumby_classic/widgets/`](../files/python/neochumby_classic/widgets/)
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

**A background job**, optional: a widget with `JOB = {"label": ..., "help": ...}` and
`job_command(options) -> argv` gets a button with that label in its customize dialog. The
web page runs the argv (no shell; the options as saved) in the background, one run at a time
per widget, in the widget's state directory, shows its last stdout lines while it runs, and
`job_explain(exit_code) -> str` says what the result means. `job_command` validates
everything it puts in the argv and raises `ValueError("why")` to refuse. A run that exits
0 asks for a render. The Pajama Sam screensaver's Convert is the example.

### `page_count` and the slideshow

The renderer plans the slideshow before anything is drawn: apps in channel order, each
app's enabled sub-slides in order, each sub-slide `page_count(subslide_id, options)`
pages (`subslide_id` is `None` for a widget without sub-slides). Whatever doesn't fit in
the engine's 128 pages (limits.py) is cut, and the web page shows what was cut. `render()` is then
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
| `clip N DIR NAME [NAME ...] [random]` | the page plays a looping clip (clip format v1, [neochumby-scummvm's docs/clip-format.md](https://github.com/phoozle/neochumby-scummvm/blob/main/docs/clip-format.md)): files `NAME.idx` and `NAME.clip` in `DIR`, which is `.` (beside the manifest: the widget writes them like any other file); with several names and `random`, a random one each time the page comes on. Provisional: the engine's directive is being added in neochumby #11, and this follows its plan |
| `flurry N [PRESET] [KEY=VALUE ...]` | the engine draws the Flurry screensaver (Calum Robinson's, BSD licence) live over the whole page: `PRESET` is `classic` (the default), `rgb`, `water`, `fire`, `psychedelic` or `random`; options `streams=1..16`, `colour=` (`red`, `magenta`, `blue`, `cyan`, `green`, `yellow`, `slowcyclic`, `cyclic`, `tiedye`, `rainbow`, `white`, `multi`, `dark`), `speed=0..4`, `thickness=1..20000`, `bright=0.0625..4`, `seed=`, `puffs=64..3600`, `rate=10..121`; an unknown key is ignored. The page's file is drawn over (the widget's is a still, for engines without the directive). In neochumby's classic engine from johnmerchant/neochumby#60 |
| `logofx N FILE W H X Y [FX [MODE [HOLD]]]` | a logo that builds itself with a demoscene intro (`random`, `fly`, `print`, `wobble`, `zoom`, `drop`, `dissolve`, `raster`, `decrunch`, `spin`, `octopus`) |

**Screens** (interactive, outside the slideshow):

| Directive | Meaning |
| --- | --- |
| `screen NAME FILE` | a 320×240 screen; `NAME_night`, if there is one, is shown instead in night mode |
| `pane SCREEN FILE W H X Y VAR FRAMES` | a picture on the screen showing the frame for state variable `VAR` (FILE: the frames stacked, `W*H*FRAMES*2` bytes). `SCREEN` may also be `page:N` or `pagenight:N`, one of the widget's own pages |
| `hot SCREEN X Y W H ACTION [ARG]` | a hot zone: a tap there does `ACTION` |
| `slider SCREEN EMPTY FULL KNOB W H X Y T0 T1 KW KH` | the volume slider |

Actions: `slideshow` (back to the slideshow, to the page a `pagetap` came from),
`screen NAME`, `chumby` (leave for the stock Chumby software), `night`, `nightmode`, `pin`,
`next_app`, `prev_app`, `secs_up`, `secs_down`, and:

- `app NAME`: open neochumby app NAME full screen (the engine asks the session to `open`
  it, and the session refuses an app it doesn't have);
- `player SCREEN`: open the Chumby's player, the app that plays its music (the session's
  `$NEOCHUMBY_PLAYER`: its `player=` setting, else the default), or show `SCREEN` if it has
  none (Home's MUSIC: `player apps_p0`);
- `player_toggle`: pause or resume the player (the engine sends `player toggle` to the
  session, which tells the player's service). The engine still takes the C engine's
  `music_toggle` for it.

State variables for panes: `player` (MUTE's face: 1 playing, 0 paused; the engine also
takes the C engine's `music`), `night`, `nightmode`, `pin`, `secs`, `app`, and the
live values `wifi` (0-4 bars) and `wifi_pct`. (Engines from before players also had
`station N`, `next_station` and the `station` directive, from when classic played radio
itself; nothing here uses them, and the current engine skips them.)

### Live values

A few things the engine reads on the Chumby itself and draws without waiting for a render
(neochumby's live-values spec, v1). A widget never draws these into its pictures: they
would be stale, and a change would make the picture new.

| Key | What | Text |
| --- | --- | --- |
| `wifi` | WiFi link quality as bars | `0`-`4`; as a pane variable, the frame |
| `wifi_pct` | the same, in per cent | `54%`; as a pane variable, 0-100 |
| `ip` | the Chumby's IPv4 address | `192.0.2.7` |
| `ssid` | the WiFi network's name | as is |
| `uptime` | time since boot | `42m`, `5h 7m`, `3d 4h` |
| `mem_free` | free memory | `33.1 MB` |
| `stick_free` | free space on the USB stick | `1.4 GB`, `830 MB` |
| `firmware` | the Chumby's firmware version | as is |
| `name` | the Chumby's name | as is |

A value the engine can't read is drawn as `?` (a pane shows frame 0). Text is cut to 32
characters.

| Directive | Meaning |
| --- | --- |
| `livefont NAME FILE CELLW CELLH [W0 .. W78]` | a font for live text: the 79 glyphs of `widget.LIVE_CHARSET` in order, each in a `CELLW`×`CELLH` cell (1-64), stacked vertically, `0xF81F` transparent; FILE is `CELLW*CELLH*79*2` bytes. The widths are the glyphs' advances, all 79 or none (monospace). It comes before the `live` lines that use it |
| `live TARGET KEY FONT X Y W H ALIGN BG` | a box where the engine draws value `KEY` in `FONT`: `TARGET` is a screen declared before, or `page:N` / `pagenight:N`; the box lies inside the screen; `ALIGN` is `left`, `right` or `center`; `BG` is `bg` (the target's own picture under the box) or `#RRGGBB` |

Draw the glyphs anti-aliased against the colour they will sit on, as Settings does
(`screens.livefont`). `python -m neochumby_classic.preview` fills the boxes with values you
give it (`ip=192.0.2.7`). Settings → Network and Chumby Info, and Home's signal bars, are
the examples. While a page shows its `pagenight` picture, its `pagenight:N` panes and boxes
are drawn if it has any, else its `page:N` ones.

**Shared by the whole slideshow** (one widget only: the first in play order that writes
one wins, and later ones are dropped): `clock FILE CELLW CELLH RIGHT Y W0..W14`,
`clocknight FILE`, `ticker FILE W H Y SPEED`, `tickernight FILE`. The Weather widget
writes these.

**Not for widgets**: `sun`, `station`, `secsopts` and `pageapp` belong to the renderer,
which also draws Home, Settings, APPS and the QR code screen. Lines starting with `#` are
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
  short form of it (Whyalla News uses `wn_`, from when the engine allowed 23 bytes).
- **Screen names** are letters, digits and `_`, at most 48 bytes with `_night` (the
  limits below).
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

  | Directive | What | Limit |
  | --- | --- | --- |
  | `page` | slideshow pages | 128 |
  | `screen` | screens (each `_night` copy counts) | 512 |
  | `slider` | volume sliders | 512 |
  | `pane` | panes, on screens and pages together | 512 |
  | `hot` | tap zones | 2048 |
  | `sprite` | sprites, all pages | 256 |
  | `actor` | actors | 128 |
  | `glow` | light layers | 32 |
  | `glowseq` | light clips (1000 frames each) | 8 |
  | `flurry` | Flurry pages | 8 |
  | `station` | music stations | 64 |
  | `sun` | sunrise/sunset days | 32 |
  | `secsopts` | slideshow timings | 12 |
  | `livefont` | live-text fonts | 32 |
  | `live` | live-text boxes | 512 |
  | (files) | different files the manifest names (glowseq frames and fonts included) | 4096 |

  And lengths: a screen name (its `_night` copy's included, so keep a base name to 42), a
  `pagetap` or `hot` argument, a pane or live target and a font name are at most 48 bytes;
  a file name 96; a glowseq prefix 88; a `hot` action 15; a pane variable 11. Positions and
  sizes are within -32768..32767, and `manifest.txt` and `hashes.txt` at most 2 MB each.
  The engine skips a line past any of these; the renderer refuses the render instead.

  With every built-in widget on, a channel uses 18 pages, 109 screens, 23 panes, 485 hot zones,
  2 live fonts and 16 live boxes.
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
