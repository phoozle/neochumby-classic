# neochumby-classic

The classic Chumby experience for [neochumby](https://github.com/johnmerchant/neochumby):
a 90s weather channel for anywhere in Australia, ABC News, stock quotes, your photos and a
Vista-style aurora screensaver, the Mac's Flurry screensaver (and, if you add them, the
Whyalla News and a Pajama Sam screensaver from your own copies of the games), with Home, Settings and Music screens, and
a "my channels" web page modelled on chumby.com's.

The Chumby (a 266 MHz ARM with no FPU) only copies pictures to its screen. Everything hard
(fetching, parsing, fonts, layout) happens here, on the server beside neochumbyd: this
program draws every page and screen ahead of time, for each paired Chumby, every ten
minutes.

| | | |
| --- | --- | --- |
| ![Current conditions](docs/screenshots/weather.png) | ![Local forecast](docs/screenshots/forecast.png) | ![Night mode](docs/screenshots/weather-night.png) |
| ![News](docs/screenshots/news.png) | ![Stock quotes](docs/screenshots/stocks.png) | ![Photos](docs/screenshots/photos.png) |
| ![Aurora screensaver](docs/screenshots/aurora.png) | ![Home](docs/screenshots/home.png) | ![Settings](docs/screenshots/settings.png) |

The screenshots come from the smoke test (`tests/smoke.py --screenshots`): made-up data
and the placeholder pictures this repository ships, put together as the Chumby draws them.

## How it fits with neochumby

```
 this repository (Python)                         neochumby (Rust)
 ┌──────────────────────────────┐                 ┌───────────────────────────────┐
 │ neochumby-classic-renderer   │  writes         │ a companion reads each        │
 │   every 10 min, and on       ├──────────────►  │ Chumby's directory and        │
 │   request, per paired Chumby │  out/<chumby>/  │ delivers it (NFS or sync)     │
 │                              │                 │                               │
 │ neochumby-classic-web        │  admin socket   │ neochumbyd: pairing, each     │
 │   "my channels", pairing,    ├──────────────►  │ Chumby's start= and delivery= │
 │   start app, delivery        │                 │                               │
 └──────────────────────────────┘                 │ on the Chumby: the classic    │
                                                  │ app draws the pictures        │
                                                  └───────────────────────────────┘
```

- **The renderer** writes each Chumby's pictures into `out/<chumby>/`: `manifest.txt`,
  RGB565 files, `hashes.txt` and a `version` marker written last.
  [docs/interface.md](docs/interface.md) is the exact contract a neochumby companion reads.
- **The web page** edits the channel (which apps, in what order, their options), and,
  after you sign in with the server owner's token, approves pairings and sets each
  Chumby's start app and delivery. It talks to neochumbyd only through neochumbyd's admin
  socket. It offers as start apps only the apps whose manifest says `start = true`; pick
  `classic` to have a Chumby start in this experience.
- Nothing in neochumby starts this program, and nothing here starts neochumbyd. They run
  side by side on the same server.

## Install

You need a Linux server running neochumbyd (with systemd), and Python 3.11 or newer with
its `venv` module (Debian: `apt install python3 python3-venv`).

```sh
git clone https://github.com/phoozle/neochumby-classic
cd neochumby-classic
sudo ./install.sh            # add --heif to accept iPhone HEIC photos
```

`install.sh`:

1. creates the user `neochumby-classic` and adds it to neochumbyd's group (`neochumbyd`;
   `--group` to change), so the renders are readable by neochumbyd's companion;
2. makes a venv in `/opt/neochumby-classic` and installs this package into it;
3. asks **once** for the server owner's token (`neochumbyd token` prints it), checks it with
   neochumbyd, and keeps it root-only in `/etc/neochumby-classic/owner-token`;
4. writes and starts two systemd services, `neochumby-classic-renderer` and
   `neochumby-classic-web`, with their settings in `/etc/neochumby-classic/env`.

Then open `http://<server>:8080/` on your phone or computer. Under the Weather app's
**customize**, choose your state, then the weather station nearest you from the list (the
forecast town is the one with the station's name, or pick another). Until you do, the
Chumby shows a page saying where to choose it. Under **Chumby settings**, set the address
the Chumby's QR code should show and your time zone. Night mode follows the sunrise and
sunset at your weather station; set a latitude and longitude there only to use another
place.

Re-run `install.sh` to upgrade (after `git pull`) or to change an option (`--help` lists
them); it keeps the settings it was given before. `sudo ./install.sh --token` stores a new
owner token after `neochumbyd token new`. `sudo ./install.sh --uninstall` removes the
services, the venv and the settings but keeps your config, photos, renders and assets;
add `--purge` to remove those and the user too. It never installs or changes neochumbyd.

**The admin socket and the dedicated user.** neochumbyd's admin socket (neochumby PR #9)
answers only neochumbyd's own user and root. Until it also lets its group in, the
`neochumby-classic` user can't use it: install.sh says so at the end. Then either run the
services as neochumbyd's user (`sudo ./install.sh --user neochumbyd`), or list the Chumbys
to render in `/etc/neochumby-classic/env` (`NEOCHUMBY_CLASSIC_CHUMBYS="kitchen lounge"`);
the web page's Chumbys page needs the socket either way.

**With Docker** instead: see [docker-compose.yml](docker-compose.yml), whose comments list
what to make first. Both containers run as a user of their own (`CLASSIC_UID`, never
neochumbyd's), with neochumbyd's group as their only group, a read-only root filesystem and
no capabilities. They don't see neochumbyd's state directory, so not its key, pairings or
owner token: only its admin socket, bound on its own, and its app tree, read-only. The web
page is published on the address you give (`CLASSIC_WEB_ADDR`, the server's LAN address),
not on every interface:

```sh
CLASSIC_UID=$(id -u neochumby-classic) NEOCHUMBYD_GID=$(getent group neochumbyd | cut -d: -f3) \
CLASSIC_WEB_ADDR=192.0.2.10 docker compose up -d
```

The socket is subject to the same rule as above: neochumbyd answers it for its group only
once it lets its group in, so until then name the Chumbys (`NEOCHUMBY_CLASSIC_CHUMBYS`, in a
`docker-compose.override.yml`). neochumbyd makes its socket again when it restarts, and the
bind keeps the old one, so run `docker compose restart` after restarting neochumbyd.

### Settings

`/etc/neochumby-classic/env` (install.sh writes it), or the environment:

| Variable | Default | |
| --- | --- | --- |
| `NEOCHUMBY_CLASSIC_STATE` | `/var/lib/neochumby-classic` | config.json, photos, caches, `refresh/`, `info/` |
| `NEOCHUMBY_CLASSIC_OUT` | `$STATE/out` | the renders, one directory per Chumby |
| `NEOCHUMBY_CLASSIC_ADMIN` | `/var/lib/neochumbyd/admin.sock` | neochumbyd's admin socket |
| `NEOCHUMBY_CLASSIC_TREE` | `/var/lib/neochumbyd/tree` | neochumbyd's app tree (the start-app list, and Home's APPS); `NEOCHUMBY_TREE` wins if set |
| `NEOCHUMBY_CLASSIC_WEB` | `0.0.0.0:8080` | where the web page listens |
| `NEOCHUMBY_CLASSIC_EVERY` | `600` | seconds between renders (at least 60) |
| `NEOCHUMBY_CLASSIC_CHUMBYS` | (ask neochumbyd) | the Chumbys to render, if not the paired ones |
| `NEOCHUMBY_CLASSIC_ASSETS` | `/etc/neochumby-classic/assets` | your own pictures (below) |
| `NEOCHUMBY_CLASSIC_CONFIG` | `$STATE/config.json` | the channel settings the web page writes |

## The original logo and footage

This repository's code contains none of the original artwork: no Chumby logo or octopus,
no broadcaster's or company's logos, no video. Each widget draws a placeholder instead.

The originals are available separately, from
[phoozle/neochumby-classic-assets](https://github.com/phoozle/neochumby-classic-assets).
**They are not covered by this repository's MIT licence.** Each file belongs to its owner,
as that repository's README says, so check it before you use them. To use them on your
server:

```sh
sudo ./install.sh --assets          # clones it into /etc/neochumby-classic/assets, or pulls it
```

or by hand:

```sh
sudo git clone https://github.com/phoozle/neochumby-classic-assets /etc/neochumby-classic/assets
```

install.sh remembers `--assets` and pulls again whenever it is re-run. `--assets URL` takes
another repository (your own copies, say), and `--uninstall --purge` removes the directory.
They replace the placeholders at the next render.

Its `weather-badge.png` is the original **Whyalla** Weather badge: it names that town, whatever
place you choose. Unless you live there, delete it from the asset directory (or keep your own
copy without it, with `--assets URL`), and the weather pages draw their own badge with your
place's name.

You can also use pictures of your own: put them in `/etc/neochumby-classic/assets/`
(`NEOCHUMBY_CLASSIC_ASSETS`), readable by the service user, under these names:

| File | Used for | Without it |
| --- | --- | --- |
| `logo.png` | the aurora's logo (RGBA, about 220 × 64) | a "neochumby" wordmark |
| `octopus.png` | the mark on Home, Settings and the leave dialog (RGBA) | a drawn octopus |
| `weather-badge.png` | the weather pages' badge, top left (RGBA, about 5:4) | our badge: your place's name, a sun and cloud, WEATHER |
| `raysclip.png` | the aurora's light rays | no rays |
| `abc-logo.png`, `abc-swirls.png` | the news header | a plain NEWS mark |
| `whyallanews-wordmark.png` | Whyalla News' masthead (its alpha is the mask, recoloured per theme) | the paper's name in Liberation Sans Bold |

- `raysclip.png` is made from a video with [`tools/raysclip.py`](tools/raysclip.py)
  (needs ffmpeg and numpy).
- The news header's two files can be cut from a banner with
  `python -m neochumby_classic.widgets.news assets BANNER.png DIR`.
- Keep pictures like these out of any fork of this repository you publish
  (`private-assets/` is in `.gitignore` for that).

## Bring your own Pajama Sam

The **Pajama Sam Screensaver** plays looping scenes from *Pajama Sam: No Need to Hide When
It's Dark Outside* and *Pajama Sam 2: Thunder and Lightning Aren't So Frightening*, made
from **your own copies of the games**, on your own server. Nothing of the games is in this
repository or its tests, and nothing is downloaded: the games are Humongous
Entertainment's, and you need your own copies.

1. Build the converter, `neochumby-pajamasam`, from
   [phoozle/neochumby-scummvm](https://github.com/phoozle/neochumby-scummvm) (Rust and a C++
   compiler: `cargo build --release --bin neochumby-pajamasam`), and put it on the PATH, or
   name it in `/etc/neochumby-classic/env`: `NEOCHUMBY_PAJAMASAM=/usr/local/bin/neochumby-pajamasam`.
2. Copy each game's files to a folder on the server that the `neochumby-classic` user can
   read, outside `/home` (the services can't see `/home`), e.g. `/srv/games/pajama1`
   (`Pajama.HE0` and the rest) and `/srv/games/pajama2` (`PAJAMA2.HE0` and the rest).
3. On the web page, **add apps → Pajama Sam Screensaver**, then **customize**: enter the
   folders, save, and press **Convert**. It plays each game headless and records the
   scenes, about a minute each; the dialog shows its progress and what the result means:

   | Exit | Meaning |
   | --- | --- |
   | 0 | Done: the clips are made (some scenes may have failed: see the progress lines) |
   | 1 | The converter didn't understand its arguments (a bug, or another version) |
   | 2 | No game recognised in the folders given |
   | 3 | The games were recognised, but no scene could be recorded |
   | 4 | The converter couldn't write its output directory |

The clips go only to this server's private state
(`/var/lib/neochumby-classic/widgets/pajamasam/clips`), and from there to your Chumbys,
which play a random one each time the screensaver comes on. Until there are clips, the
screensaver shows a page saying where to add the files (or nothing, if you switch that off).
Clips you converted elsewhere can go in an asset directory's `pajamasam/` instead (with
their `clips.txt`). With Docker, mount the game folders into the web container read-only.
Never publish the clips: they are copies of the games' art.

## Data sources: terms to check

The widgets fetch from these sources. Check each one's terms before you use this beyond
your own home, and certainly before anyone redistributes what it draws.

| Source | Used by | To check |
| --- | --- | --- |
| Bureau of Meteorology, anonymous FTP (`ftp.bom.gov.au/anon/gen/fwo/`): each state's observations and précis town forecasts | Weather, the ticker, the web page's station and town lists | BOM's copyright and data terms for display and redistribution. Its website blocks scrapers; the FTP products are the sanctioned route |
| ABC News RSS, and ABC article pages (`abc.net.au`) | News | ABC's RSS terms (personal, non-commercial use). The Eyre Peninsula page and the article bodies are **scraped**, which the terms may not allow |
| The Whyalla News' WordPress RSS feed (`whyallanewsonline.com.au/feed/`) and the photos it links | Whyalla News | The paper's terms. The widget reads only the RSS feed (the site refuses scripts, and it doesn't try to get round that) and **respects the paywall**: a subscriber-only story shows only the free excerpt the feed carries, ending "Continue at whyallanewsonline.com.au"; full text only where the feed itself carries it |
| Yahoo Finance's chart endpoint (`query1.finance.yahoo.com/v8/finance/chart`) | Stocks | Unofficial and undocumented. Yahoo's terms allow personal use, not redistribution |
| financialmodelingprep.com, assets.parqet.com | Stock logos | Unofficial. The logos are the companies' trademarks; they are fetched and cached on your server, never committed |
| SomaFM's API and streams | Music | SomaFM's terms for its API and channel artwork |

Flurry is the one thing the Chumby draws itself: Calum Robinson's Mac OS X screensaver
(BSD licence), ported to fixed point in neochumby's classic engine and run live, at 25 frames
a second in about half the Chumby's CPU. This program only writes the page and its options
(preset, streams, colours, speed); nothing of Apple's is used.

The weather has no default place: you choose it. Whyalla (South Australia) appears only as an
example, in the help text and the tests. The Whyalla News is that town's paper, an optional
widget: it is off until you add it from "add apps" on the web page.

## Widgets

The eight widgets are plugins, found through Python entry points, exactly as a widget from
another repository would be. [docs/widgets.md](docs/widgets.md) is the widget API
(version 1): what a widget declares, what it may draw, the rules that keep the Chumby fast
(stable names, write only what changed, no per-render times, a memory budget per page,
the engine's limits), and how to publish one in its own repository and install it pinned
to a commit (`sudo ./install.sh --widget git+https://...@<commit>`).

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/python tests/smoke.py --screenshots docs/screenshots
```

The smoke test renders every widget offline with made-up data (`tests/fakenet.py`), checks
the output against [docs/interface.md](docs/interface.md), checks that a second render
writes nothing, and drives the web page against a stand-in for neochumbyd's admin socket.
`python -m neochumby_classic.preview OUT page 0 page.png` puts one page together as the
Chumby would show it. CI runs all of this, the install script on a real systemd, and the
Docker build.

[docs/packaging.md](docs/packaging.md) describes how this may also become a neochumby app
crate.

## Licence

MIT: see [LICENSE](LICENSE). The bundled fonts keep their own licences, which are next to
them in [`files/python/neochumby_classic/data/fonts/`](files/python/neochumby_classic/data/fonts/).
